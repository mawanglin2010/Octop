"""Fork subscription-plan media providers (Ark Agent Plan / Model Studio Token Plan)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from octop_harness import MediaGenerationConfig, MediaProviderConfig
from octop_harness.media import (
    ImageGenerationRequest,
    VideoGenerationRequest,
    create_media_provider,
)
from octop_harness.media.providers.dashscope import DashScopeMediaProvider
from octop_harness.media.providers.volcengine import VolcengineMediaProvider

from octop.infra.agents.media_fork import IMAGE_ONLY_MEDIA_PROVIDERS
from octop.infra.agents.media_fork.ark_plan import ArkAgentPlanMediaProvider
from octop.infra.agents.settings.media_generation import (
    MEDIA_PROVIDER_PRESETS,
    MediaGenerationSettingsStore,
    MediaProviderUpdate,
    verify_media_credentials,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.settings import SettingsRepo

_PNG = b"\x89PNG\r\n\x1a\nimage-bytes"
_B64 = base64.b64encode(_PNG).decode()
_PLANS = ("volcengine-agent-plan", "dashscope-token-plan")


@pytest.fixture
def store(tmp_path: Path) -> MediaGenerationSettingsStore:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    return MediaGenerationSettingsStore(settings_repo=SettingsRepo(db), secret_repo=SecretRepo(db))


def _update(provider: str) -> MediaProviderUpdate:
    preset = MEDIA_PROVIDER_PRESETS[provider]  # type: ignore[index]
    return MediaProviderUpdate(
        id=provider,
        provider=provider,  # type: ignore[arg-type]
        display_name=preset.display_name,
        enabled=True,
        base_url=preset.base_url,
        image_enabled=True,
        video_enabled=True,
        image_model=preset.default_image_model,
        video_model=preset.default_video_model,
        api_key=f"{provider}-secret-test",
    )


@pytest.mark.parametrize(
    ("provider", "provider_type", "base_url", "image_model", "video_model"),
    [
        (
            "volcengine-agent-plan",
            ArkAgentPlanMediaProvider,
            "https://ark.cn-beijing.volces.com/api/plan/v3",
            "doubao-seedream-5-0-pro",
            "doubao-seedance-2.0-fast",
        ),
        (
            "dashscope-token-plan",
            DashScopeMediaProvider,
            "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1",
            "wan2.7-image-pro",
            "wan3.0-video",
        ),
    ],
)
def test_plan_presets_reuse_stock_adapters(
    provider: str, provider_type: type[object], base_url: str, image_model: str, video_model: str
) -> None:
    assert provider not in IMAGE_ONLY_MEDIA_PROVIDERS
    preset = MEDIA_PROVIDER_PRESETS[provider]  # type: ignore[index]
    assert (preset.base_url, preset.default_image_model, preset.default_video_model) == (
        base_url,
        image_model,
        video_model,
    )
    config = MediaProviderConfig(id=provider, provider=provider, api_key="k")  # type: ignore[arg-type]
    assert config.base_url == base_url
    assert isinstance(create_media_provider(config), provider_type)


def test_saved_plan_providers_route_image_and_video(store: MediaGenerationSettingsStore) -> None:
    view = store.save(
        enabled=True,
        providers=[_update(name) for name in _PLANS],
        default_image_provider="dashscope-token-plan",
        default_video_provider="volcengine-agent-plan",
    )
    assert all(item.video_enabled for item in view.providers)

    config = store.harness_config()
    assert isinstance(config, MediaGenerationConfig)
    assert [item.provider for item in config.providers] == list(_PLANS)
    assert config.has_image_provider() is True
    assert config.has_video_provider() is True


@pytest.mark.parametrize(
    ("provider", "base_url", "expected_url"),
    [
        (
            "volcengine-agent-plan",
            "https://ark.cn-beijing.volces.com/api/plan/v3",
            "https://ark.cn-beijing.volces.com/ping",
        ),
        (
            "dashscope-token-plan",
            "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1",
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models",
        ),
    ],
)
@pytest.mark.asyncio
async def test_plan_credential_probe_urls(provider: str, base_url: str, expected_url: str) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await verify_media_credentials(
            provider,  # type: ignore[arg-type]
            "plan-key",
            base_url=base_url,
            client=client,
        )

    assert result == {"ok": True}
    assert seen == {"url": expected_url, "auth": "Bearer plan-key"}


@pytest.mark.asyncio
async def test_agent_plan_calls_plan_endpoints() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": _B64}]})
        return httpx.Response(200, json={"id": "cgt-1"})

    config = MediaProviderConfig(id="ap", provider="volcengine-agent-plan", api_key="k")  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = create_media_provider(config, client=client)
        images = await adapter.generate_image(ImageGenerationRequest(prompt="cat"))
        submission = await adapter.submit_video(VideoGenerationRequest(prompt="cat runs"))

    assert images[0].data == _PNG
    assert submission.task is not None and submission.task.task_id == "cgt-1"
    (image_path, image_body), (video_path, video_body) = seen
    assert image_path == "/api/plan/v3/images/generations"
    assert image_body["model"] == "doubao-seedream-5-0-pro"
    assert image_body["size"] == "2K"
    assert "sequential_image_generation" not in image_body
    assert video_path == "/api/plan/v3/contents/generations/tasks"
    assert video_body["model"] == "doubao-seedance-2.0-fast"


@pytest.mark.asyncio
async def test_token_plan_calls_plan_host() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((f"{request.url.host}{request.url.path}", json.loads(request.content)))
        if request.url.path.endswith("/multimodal-generation/generation"):
            content = [{"image": f"data:image/png;base64,{_B64}"}]
            return httpx.Response(
                200, json={"output": {"choices": [{"message": {"content": content}}]}}
            )
        return httpx.Response(200, json={"output": {"task_id": "t-1", "task_status": "PENDING"}})

    config = MediaProviderConfig(
        id="tp",
        provider="dashscope-token-plan",  # type: ignore[arg-type]
        api_key="k",
        video_model="happyhorse-1.1-t2v",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = create_media_provider(config, client=client)
        await adapter.submit_image(ImageGenerationRequest(prompt="cat"))
        submission = await adapter.submit_video(VideoGenerationRequest(prompt="cat runs"))

    assert submission.task is not None and submission.task.task_id == "t-1"
    (image_url, image_body), (video_url, video_body) = seen
    host = "token-plan.cn-beijing.maas.aliyuncs.com"
    assert image_url == f"{host}/api/v1/services/aigc/multimodal-generation/generation"
    assert image_body["model"] == "wan2.7-image-pro"
    assert video_url == f"{host}/api/v1/services/aigc/video-generation/video-synthesis"
    assert video_body["model"] == "happyhorse-1.1-t2v"
    assert video_body["input"] == {"prompt": "cat runs"}
    assert video_body["parameters"]["resolution"] == "720P"


@pytest.mark.parametrize(
    ("size", "expected_size"),
    [(None, "2K"), ("1024x1024", "2K"), ("2048x2048", "2048x2048")],
)
@pytest.mark.asyncio
async def test_agent_plan_image_body_fits_plan_endpoint(
    size: str | None, expected_size: str
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    config = MediaProviderConfig(
        id="ap",
        provider="volcengine-agent-plan",  # type: ignore[arg-type]
        api_key="k",
        image_model="doubao-seedream-5.0-flash",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = create_media_provider(config, client=client)
        await adapter.generate_image(ImageGenerationRequest(prompt="cat", size=size))

    (body,) = bodies
    assert body["size"] == expected_size
    assert "sequential_image_generation" not in body
    assert "sequential_image_generation_options" not in body


@pytest.mark.parametrize(("seed", "expected_seeds"), [(None, [None] * 3), (7, [7, 8, 9])])
@pytest.mark.asyncio
async def test_agent_plan_multi_image_becomes_single_requests(
    seed: int | None, expected_seeds: list[int | None]
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    config = MediaProviderConfig(
        id="ap",
        provider="volcengine-agent-plan",  # type: ignore[arg-type]
        api_key="k",
        image_model="doubao-seedream-5.0-flash",
    )
    request = ImageGenerationRequest(prompt="cat", count=3, seed=seed)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = create_media_provider(config, client=client)
        images = await adapter.generate_image(request)
        submission = await adapter.submit_image(request)

    assert len(images) == 3
    assert len(submission.artifacts) == 3
    assert len(bodies) == 6
    for body in bodies:
        assert "sequential_image_generation" not in body
        assert "sequential_image_generation_options" not in body
    assert [body.get("seed") for body in bodies[:3]] == expected_seeds


@pytest.mark.asyncio
async def test_standard_volcengine_body_unchanged() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": _B64}]})

    config = MediaProviderConfig(id="v", provider="volcengine", api_key="k")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = create_media_provider(config, client=client)
        assert type(adapter) is VolcengineMediaProvider
        await adapter.generate_image(ImageGenerationRequest(prompt="cat"))

    assert bodies[0]["sequential_image_generation"] == "disabled"
