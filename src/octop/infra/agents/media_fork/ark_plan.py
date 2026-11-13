"""Volcengine Ark Agent Plan media adapter (fork-only).

The plan endpoint (``/api/plan/v3``) takes the same request bodies as standard Ark, with one
exception found against the live endpoint on 2026-11-12/13: Seedream 5.0 rejects the group-image
fields with HTTP 400 ``InvalidParameter``. ``sequential_image_generation`` fails as ``"disabled"``
and as ``"auto"``, and ``sequential_image_generation_options`` fails on its own. The harness adapter
always sends the first and adds the second for ``count > 1``, so both are dropped here and a
multi-image request becomes ``count`` single-image requests (sent one after another; with a seed,
image ``i`` uses ``seed + i``). Each image is billed separately.

The harness only applies the Seedream 5 minimum-size upgrade to ``doubao-seedream-5-0*`` names;
the plan docs also spell them ``doubao-seedream-5.0-*``, so both spellings get the upgrade here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import httpx
from octop_harness.media.errors import MediaGenerationError
from octop_harness.media.models import ImageGenerationRequest, MediaSubmission, RemoteArtifact
from octop_harness.media.providers.volcengine import (
    VolcengineMediaProvider,
    _seedream_image_size,  # noqa: PLC2701 - fork bridge, see module docstring
)

_DOTTED_SEEDREAM_5 = "doubao-seedream-5.0"
_HYPHEN_SEEDREAM_5 = "doubao-seedream-5-0"
_GROUP_IMAGE_FIELDS = ("sequential_image_generation", "sequential_image_generation_options")


class ArkAgentPlanMediaProvider(VolcengineMediaProvider):
    """Ark Agent Plan: stock Volcengine adapter with plan-compatible image bodies."""

    async def _submit_image(
        self,
        client: httpx.AsyncClient,
        request: ImageGenerationRequest,
    ) -> MediaSubmission:
        if request.count <= 1:
            return await super()._submit_image(client, request)
        artifacts: list[RemoteArtifact] = []
        for index in range(request.count):
            single = replace(
                request,
                count=1,
                seed=None if request.seed is None else request.seed + index,
            )
            submission = await super()._submit_image(client, single)
            if not submission.artifacts:
                raise MediaGenerationError("Volcengine image generation returned no images")
            artifacts.extend(submission.artifacts)
        return MediaSubmission(artifacts=tuple(artifacts))

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        provider_task_id: str | None = None,
    ) -> dict[str, Any]:
        if path == "/images/generations" and json is not None:
            json = _plan_image_body(json)
        return await super()._request_json(
            client,
            method,
            path,
            json=json,
            headers=headers,
            provider_task_id=provider_task_id,
        )


def _plan_image_body(body: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    for field in _GROUP_IMAGE_FIELDS:
        out.pop(field, None)
    model = str(out.get("model") or "")
    if model.startswith(_DOTTED_SEEDREAM_5):
        size = _seedream_image_size(
            _HYPHEN_SEEDREAM_5 + model[len(_DOTTED_SEEDREAM_5) :], out.get("size")
        )
        if size:
            out["size"] = size
    return out
