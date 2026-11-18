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

// --- experts ---------------------------------------------------------------

export interface WorkBuddyExpertCard {
  plugin: string;
  template_id: string;
  nickname: WbText;
  profession: WbText;
  description: WbText;
  category_id: string;
  category: WbText;
  tags: { zh?: string[]; en?: string[] };
  quick_prompts: { zh?: string[]; en?: string[] };
  avatar_url: string | null;
  author: string;
  updated_at: string;
}

export interface WorkBuddyExpertList {
  items: WorkBuddyExpertCard[];
  categories: (WbText & { id: string })[];
  version: string;
}

export type WorkBuddyMcpStatus =
  | "ok"
  | "internal"
  | "stdio"
  | "sse"
  | "needs_secret"
  | "invalid";

export interface WorkBuddyExpertDetail extends WorkBuddyExpertCard {
  prompt: string;
  skills: { name: string; description: string; file_count: number }[];
  mcp: {
    name: string;
    transport: string;
    target: string;
    status: WorkBuddyMcpStatus;
  }[];
  connectors: string[];
}

export interface WorkBuddyPrepareResult {
  expert_id: string;
  mcp_servers: string[];
  skipped_mcp: { name: string; reason: string }[];
  connectors: string[];
}

export const workbuddyExpertsApi = {
  list: (
    params: { q?: string; category?: string; refresh?: boolean } = {},
    signal?: AbortSignal,
  ) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.category) qs.set("category", params.category);
    if (params.refresh) qs.set("refresh", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<WorkBuddyExpertList>(`/workbuddy/experts${suffix}`, {
      signal,
    });
  },
  get: (plugin: string) =>
    request<WorkBuddyExpertDetail>(
      `/workbuddy/experts/${encodeURIComponent(plugin)}`,
    ),
  prepare: (plugin: string, addMcp = true) =>
    request<WorkBuddyPrepareResult>(
      `/workbuddy/experts/${encodeURIComponent(plugin)}/prepare`,
      { method: "POST", body: JSON.stringify({ add_mcp: addMcp }) },
    ),
};
