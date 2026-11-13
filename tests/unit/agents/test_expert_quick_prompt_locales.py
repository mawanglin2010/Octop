"""Bundled experts' quick-prompt cards keep Chinese in ``zh`` and English in ``en``."""

from __future__ import annotations

import json
import re

import pytest

from octop.infra.agents.experts.catalog import default_library_root

_CJK = re.compile(r"[一-鿿]")
_MANIFESTS = sorted(default_library_root().glob("*/manifest.json"))


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_quick_prompt_english_fields_have_no_chinese(manifest) -> None:  # type: ignore[no-untyped-def]
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for i, card in enumerate(data.get("quick_prompts", [])):
        for key in ("title", "description"):  # prompts may name Chinese folders
            text = card.get(key, {}).get("en", "")
            assert not _CJK.search(text), f"quick_prompts[{i}].{key}.en is Chinese: {text!r}"
