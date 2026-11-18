"""Unit tests for harness-backed provider presets."""

from __future__ import annotations

from octop.infra.agents.providers.presets import load_provider_presets


def test_load_provider_presets_integration() -> None:
    presets = load_provider_presets()
    ids = {p["id"] for p in presets}
    assert "moonshot" not in ids
    assert "kimi-cn" in ids
    assert "minimax-intl" in ids
    assert "zhipu-intl-codingplan" in ids
    assert "siliconflow-intl" in ids

    deepseek = next(p for p in presets if p["id"] == "deepseek")
    deepseek_ids = {m["id"] for m in deepseek["models"]}
    # Harness 1.0.5 renamed the flash catalog id; v4-pro keeps its name.
    assert "deepseek-flash" in deepseek_ids
    assert "deepseek-v4-pro" in deepseek_ids
    flash = next(m for m in deepseek["models"] if m["id"] == "deepseek-flash")
    assert flash["reasoning_config"]["adapter"] == "thinking"

    token_plan = next(p for p in presets if p["id"] == "tencent-token-plan")
    token_ids = {m["id"] for m in token_plan["models"]}
    # Match by prefix: octop-harness tags token-plan model ids with a release
    # date suffix (e.g. deepseek-v4-flash-202605) and bumps catalog entries
    # over time, so assert the model family is present rather than an exact id.
    assert any(mid.startswith("deepseek-v4-flash") for mid in token_ids)
    assert any(mid.startswith("deepseek-v4-pro") for mid in token_ids)
    assert "tc-code-latest" in token_ids
    assert token_plan.get("vendor") == "tencent"
    assert token_plan.get("provider_group") == "tencent"
    assert token_plan.get("provider_variant") == "token_plan"
    token_deepseek = next(m for m in token_plan["models"] if m["id"].startswith("deepseek-v4"))
    assert token_deepseek["reasoning_config"]["adapter"] == "thinking_nested_effort"

    enterprise = next(p for p in presets if p["id"] == "tencent-token-plan-enterprise-cn")
    enterprise_ids = {m["id"] for m in enterprise["models"]}
    assert enterprise["base_url"] == "https://tokenhub.tencentmaas.com/plan/v3"
    assert enterprise.get("provider_group") == "tencent"
    assert enterprise.get("provider_variant") == "token_plan_enterprise_cn"
    # fork: follows the professional plan page (2026-11-04): no plain deepseek-v4-flash or kimi-k2.5 any more.
    assert {"auto", "deepseek-v4-pro", "kimi-k3"} <= enterprise_ids
    assert any(mid.startswith("glm-5") for mid in enterprise_ids)
    assert any(mid.startswith("minimax-m") for mid in enterprise_ids)
    # Enterprise ids are bare model names, not vendor-prefixed routes.
    assert not any("/" in model_id for model_id in enterprise_ids)
    enterprise_deepseek = next(
        m for m in enterprise["models"] if m["id"].startswith("deepseek-v4-pro")
    )
    assert enterprise_deepseek["reasoning_config"]["adapter"] == "thinking_nested_effort"

    hy_plan = next(p for p in presets if p["id"] == "tencent-hy-token-plan")
    assert hy_plan["base_url"] == "https://api.lkeap.cloud.tencent.com/plan/v3"
    assert hy_plan.get("provider_group") == "tencent"
    assert hy_plan.get("provider_variant") == "hy_token_plan"
    hy_ids = {m["id"] for m in hy_plan["models"]}
    # fork: Hy Token Plan overview (2026-11-04) lists Hy4, Hy4 preview and Hy3; hy3-preview is gone.
    assert {"hy3", "hy4-preview"} <= hy_ids and "hy3-preview" not in hy_ids
    hy3 = next(m for m in hy_plan["models"] if m["id"] == "hy3")
    assert hy3["reasoning_config"]["adapter"] == "status_only"

    hai = next(p for p in presets if p["id"] == "tencent-hai")
    assert hai["base_url"] == "https://api.model.haihub.cn/v1"
    assert hai.get("provider_variant") == "hai"
    # Harness owns the catalog ids and token limits. Octop only overlays reasoning.
    hai_models = {m["id"]: m for m in hai["models"]}
    assert hai_models["DeepSeek-V4-Pro"]["reasoning_config"]["adapter"] == "thinking"
    assert hai_models["deepseek-flash"]["reasoning_config"]["adapter"] == "thinking"
    assert hai_models["DeepSeek-V3.1-Terminus-W4AFP8"]["reasoning_config"]["adapter"] == "thinking"
    assert hai_models["Kimi-K2.5"]["reasoning_config"]["adapter"] == "thinking"
    assert hai_models["MiniMax-M2.7"]["reasoning_config"]["adapter"] == "status_only"

    coding_plan = next(p for p in presets if p["id"] == "tencent-coding-plan")
    # fork: the Coding Plan overview (2026-10-15) lists only Auto; kimi-k2.5 is no longer offered.
    assert "tc-code-latest" in {m["id"] for m in coding_plan["models"]}

    openai = next(p for p in presets if p["id"] == "openai")
    # fork: gpt-4o and the other GPT-4 rows are removed (shutdown 2027-01-14); gpt-5.2 sees images too.
    assert not {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"} & {
        m["id"] for m in openai["models"]
    }
    # fork: gpt-5 is retired and no longer in the preset; gpt-5.2 keeps the same coverage.
    gpt5 = next(m for m in openai["models"] if m["id"] == "gpt-5.2")
    assert gpt5["reasoning_config"]["adapter"] == "openai_reasoning_effort"

    kimi_cn = next(p for p in presets if p["id"] == "kimi-cn")
    # fork: kimi-k2.5 is retired and no longer in the preset; K3.1 Pro is multimodal.
    kimi_k31 = next(m for m in kimi_cn["models"] if m["id"] == "kimi-k3.1-pro")
    assert kimi_k31.get("input") == ["text", "image", "video"]
    assert kimi_cn.get("vendor") == "kimi"

    volc_open = next(p for p in presets if p["id"] == "volcengine-cn")
    assert len(volc_open["models"]) >= 6  # fork: retired ids removed, 2.1 family added

    coding = next(p for p in presets if p["id"] == "volcengine-cn-codingplan")
    coding_ids = {m["id"] for m in coding["models"]}
    # fork: Coding Plan page lists lowercase Model Names; kimi-k2.6 is not offered any more.
    assert {"deepseek-v4-flash", "kimi-k3.1-pro"} <= coding_ids
    assert "DeepSeek-V4-Flash" not in coding_ids and "kimi-k2.6" not in coding_ids

    opencode_ids = {
        "opencode-zen-openai",
        "opencode-zen-anthropic",
        "opencode-go-openai",
        "opencode-go-anthropic",
    }
    assert opencode_ids <= ids

    zen_oai = next(p for p in presets if p["id"] == "opencode-zen-openai")
    assert zen_oai["base_url"] == "https://opencode.ai/zen/v1"
    assert zen_oai.get("provider_group") == "opencode"
    assert zen_oai.get("provider_variant") == "zen_compatible"
    assert zen_oai.get("protocol") == "openai"

    zen_ant = next(p for p in presets if p["id"] == "opencode-zen-anthropic")
    assert zen_ant["base_url"] == "https://opencode.ai/zen"
    assert zen_ant.get("protocol") == "anthropic"

    go_oai = next(p for p in presets if p["id"] == "opencode-go-openai")
    assert go_oai["base_url"] == "https://opencode.ai/zen/go/v1"

    magpie = next(p for p in presets if p["id"] == "magpie")
    assert magpie["name"] == "Magpie"
    assert magpie["base_url"] == "http://127.0.0.1:3425/v1"
    assert magpie["protocol"] == "openai"
    assert magpie["models"] == []
    assert magpie["integration"] == "magpie"
    assert magpie["default_api_key"] == "magpie-octop"

    go_ant = next(p for p in presets if p["id"] == "opencode-go-anthropic")
    assert go_ant["base_url"] == "https://opencode.ai/zen/go"


def test_embedding_cloud_presets_present() -> None:
    presets = load_provider_presets()
    onnx = next(p for p in presets if p["id"] == "onnx")
    assert onnx.get("purpose") == "embedding"
    emb_ids = {p["id"] for p in presets if p.get("purpose") == "embedding" and p["id"] != "onnx"}
    assert emb_ids == {"siliconflow-embedding-cn", "dashscope-embedding"}
    assert "openai-embedding" not in emb_ids

    silicon = next(p for p in presets if p["id"] == "siliconflow-embedding-cn")
    assert silicon["name"] == "SiliconFlow"
    assert "Embeddings" not in silicon["name"]
    silicon_ids = {m["id"] for m in silicon["models"]}
    assert "Qwen/Qwen3-Embedding-8B" in silicon_ids
    assert "BAAI/bge-m3" in silicon_ids
    assert all(m.get("embedding") is True for m in silicon["models"])

    dashscope = next(p for p in presets if p["id"] == "dashscope-embedding")
    assert dashscope["name"] == "DashScope"
    dash_ids = {m["id"] for m in dashscope["models"]}
    assert "qwen3.7-text-embedding" in dash_ids
    assert "text-embedding-v4" in dash_ids
