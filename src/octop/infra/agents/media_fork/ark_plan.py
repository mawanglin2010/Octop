"""Volcengine Ark Agent Plan media adapter (fork-only).

The plan endpoint (``/api/plan/v3``) takes the same request bodies as standard Ark, with one
exception found against the live endpoint on 2026-11-12: ``doubao-seedream-5.0-flash`` rejects
``sequential_image_generation`` with HTTP 400 ``InvalidParameter`` even when it is ``"disabled"``.
The harness adapter always sends it, so single-image requests drop it here. ``"disabled"`` is the
Ark default, so the standard endpoint behaves the same without it.

The harness only applies the Seedream 5 minimum-size upgrade to ``doubao-seedream-5-0*`` names;
the plan docs also spell them ``doubao-seedream-5.0-*``, so both spellings get the upgrade here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from octop_harness.media.providers.volcengine import (
    VolcengineMediaProvider,
    _seedream_image_size,  # noqa: PLC2701 - fork bridge, see module docstring
)

_DOTTED_SEEDREAM_5 = "doubao-seedream-5.0"
_HYPHEN_SEEDREAM_5 = "doubao-seedream-5-0"


class ArkAgentPlanMediaProvider(VolcengineMediaProvider):
    """Ark Agent Plan: stock Volcengine adapter with plan-compatible image bodies."""

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
    if out.get("sequential_image_generation") == "disabled":
        del out["sequential_image_generation"]
    model = str(out.get("model") or "")
    if model.startswith(_DOTTED_SEEDREAM_5):
        size = _seedream_image_size(
            _HYPHEN_SEEDREAM_5 + model[len(_DOTTED_SEEDREAM_5) :], out.get("size")
        )
        if size:
            out["size"] = size
    return out
