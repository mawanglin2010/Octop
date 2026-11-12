"""Fork-owned media providers: OpenAI / Gemini / Tencent image adapters and two plan aliases.

These three modules are verbatim copies of the adapters proposed upstream in
TencentCloud/octop-harness PRs #255 (OpenAI), #256 (Gemini) and #257 (Tencent). Until a harness
release ships them, :func:`install_fork_media_providers` registers them into the installed harness:

* ``octop_harness.media.registry.BUILTIN_MEDIA_PROVIDERS`` gets the adapter classes, so
  ``create_media_provider`` / ``create_media_manager`` can build them;
* ``octop_harness.config._MEDIA_PROVIDER_DEFAULTS`` gets the key env, base URL and default models,
  which ``MediaProviderConfig`` validates the provider name against.

The three providers are image-only. The upstream PRs force ``video_enabled`` off inside the harness
config; here Octop's media settings do that before building a config (see
``IMAGE_ONLY_MEDIA_PROVIDERS``). Remove the three image-only adapters once the harness pin includes the PRs.

Two more entries reuse the harness's own adapters under a subscription-plan base URL:
``volcengine-agent-plan`` (Volcengine Ark Agent Plan, ``/api/plan/v3``, dedicated plan key) and
``dashscope-token-plan`` (Alibaba Cloud Model Studio Token Plan, ``token-plan.cn-beijing.maas``
host, ``sk-sp-`` key). Their endpoints and request bodies match the standard Ark / DashScope APIs
per the plan docs (read 2026-11-12), so only the provider name, base URL and model list differ.
The one exception is Agent Plan image bodies, adjusted by ``ArkAgentPlanMediaProvider`` (see
``ark_plan.py``). These are not part of the upstream PRs.
"""

from __future__ import annotations

from octop_harness.media.base import BaseMediaProvider
from octop_harness.media.providers.dashscope import DashScopeMediaProvider

from octop.infra.agents.media_fork.ark_plan import ArkAgentPlanMediaProvider
from octop.infra.agents.media_fork.gemini import GeminiMediaProvider
from octop.infra.agents.media_fork.openai import OpenAIMediaProvider
from octop.infra.agents.media_fork.tencent import TencentMediaProvider

# provider -> (api key env, base URL, default image model, default video model)
FORK_MEDIA_PROVIDER_DEFAULTS: dict[str, tuple[str, str, str, str]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-image-2.6-flare", ""),
    "gemini": (
        "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-nano-banana-2.1",
        "",
    ),
    "tencent": (
        "TENCENT_TOKENHUB_API_KEY",
        "https://tokenhub.tencentmaas.com/v1",
        "hy-image-v3.5-preview",
        "",
    ),
    "volcengine-agent-plan": (
        "ARK_AGENT_PLAN_API_KEY",
        "https://ark.cn-beijing.volces.com/api/plan/v3",
        "doubao-seedream-5-0-pro",
        "doubao-seedance-2.0-fast",
    ),
    "dashscope-token-plan": (
        "DASHSCOPE_TOKEN_PLAN_API_KEY",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1",
        "wan2.7-image-pro",
        "wan3.0-video",
    ),
}
FORK_MEDIA_PROVIDERS: dict[str, type[BaseMediaProvider]] = {
    "openai": OpenAIMediaProvider,
    "gemini": GeminiMediaProvider,
    "tencent": TencentMediaProvider,
    "volcengine-agent-plan": ArkAgentPlanMediaProvider,
    "dashscope-token-plan": DashScopeMediaProvider,
}
IMAGE_ONLY_MEDIA_PROVIDERS: frozenset[str] = frozenset({"openai", "gemini", "tencent"})


def install_fork_media_providers() -> None:
    """Register the fork adapters into the installed harness (idempotent).

    Entries the harness already defines are left alone, so a harness release that ships the
    upstream adapters takes precedence over these copies.
    """
    from octop_harness import config as harness_config  # noqa: PLC0415
    from octop_harness.media import registry  # noqa: PLC0415

    defaults = harness_config._MEDIA_PROVIDER_DEFAULTS  # noqa: SLF001 - fork bridge, see module docstring
    for name, provider_class in FORK_MEDIA_PROVIDERS.items():
        registry.BUILTIN_MEDIA_PROVIDERS.setdefault(name, provider_class)
        defaults.setdefault(name, FORK_MEDIA_PROVIDER_DEFAULTS[name])


__all__ = [
    "ArkAgentPlanMediaProvider",
    "FORK_MEDIA_PROVIDERS",
    "FORK_MEDIA_PROVIDER_DEFAULTS",
    "IMAGE_ONLY_MEDIA_PROVIDERS",
    "GeminiMediaProvider",
    "OpenAIMediaProvider",
    "TencentMediaProvider",
    "install_fork_media_providers",
]
