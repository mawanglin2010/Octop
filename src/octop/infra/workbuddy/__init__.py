"""WorkBuddy market import (fork).

Tencent WorkBuddy publishes its skill, connector and expert markets as public
CDN files. This package fetches them on demand, caches them under
``~/.octop/workbuddy_market/`` and converts entries into Octop's own skill,
expert and connector shapes. Nothing from those markets is bundled with Octop.
"""
