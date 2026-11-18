// fork: WorkBuddy market import (see src/octop/api/routers/workbuddy_market.py)
import { request } from "../request";

export interface WbText {
  zh?: string | null;
  en?: string | null;
}

export interface WorkBuddySkillCard {
  slug: string;
  name: WbText;
  summary: WbText;
  description: string;
  category: WbText;
  tags: { zh?: string[]; en?: string[] };
  examples: { zh?: string[]; en?: string[] };
  featured: boolean;
  version: string;
  icon_url: string | null;
  mentions_workbuddy: boolean;
  file_count: number;
  total_bytes: number;
}

export interface WorkBuddySkillList {
  items: WorkBuddySkillCard[];
  categories: WbText[];
  version: string;
}

export interface WorkBuddySkillDetail extends WorkBuddySkillCard {
  skill_md: string;
  files: { path: string; size: number }[];
}

export type WorkBuddyInstallTarget =
  | { type: "agent"; agentId: string }
  | { type: "package"; packageId: string };

export const workbuddyMarketApi = {
  listSkills: (
    params: { q?: string; category?: string; refresh?: boolean } = {},
    signal?: AbortSignal,
  ) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.category) qs.set("category", params.category);
    if (params.refresh) qs.set("refresh", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<WorkBuddySkillList>(`/workbuddy/skills${suffix}`, {
      signal,
    });
  },
  getSkill: (slug: string) =>
    request<WorkBuddySkillDetail>(
      `/workbuddy/skills/${encodeURIComponent(slug)}`,
    ),
  installSkill: (target: WorkBuddyInstallTarget, slug: string) => {
    const path =
      target.type === "agent"
        ? `/workbuddy/skills/install/agents/${encodeURIComponent(
            target.agentId,
          )}`
        : `/workbuddy/skills/install/packages/${encodeURIComponent(
            target.packageId,
          )}`;
    return request<{ installed: boolean; name: string }>(path, {
      method: "POST",
      body: JSON.stringify({ slug, overwrite: true, enable: true }),
    });
  },
};
