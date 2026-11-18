// fork: zh/en strings for the WorkBuddy market UI without touching the
// upstream locale bundles (they change on nearly every upstream release).
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { WbText } from "../../api/modules/workbuddyMarket";

export function isZhLanguage(lang: string | undefined): boolean {
  return (lang || "").toLowerCase().startsWith("zh");
}

/** ``tx("中文", "English")`` picks by the active UI language. */
export function useWbText() {
  const { i18n } = useTranslation();
  const zh = isZhLanguage(i18n.language);
  const tx = useCallback(
    (zhText: string, enText: string) => (zh ? zhText : enText),
    [zh],
  );
  const pick = useCallback(
    (value: WbText | null | undefined, fallback = "") => {
      const first = zh ? value?.zh : value?.en;
      const second = zh ? value?.en : value?.zh;
      return (first || second || fallback).trim();
    },
    [zh],
  );
  const pickList = useCallback(
    (value: { zh?: string[]; en?: string[] } | null | undefined) => {
      const first = zh ? value?.zh : value?.en;
      const second = zh ? value?.en : value?.zh;
      return first?.length ? first : second ?? [];
    },
    [zh],
  );
  return { zh, tx, pick, pickList };
}

/** Drop the YAML frontmatter so the drawer shows the skill body. */
export function stripFrontmatter(markdown: string): string {
  const match = markdown.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return match ? markdown.slice(match[0].length).trimStart() : markdown;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
