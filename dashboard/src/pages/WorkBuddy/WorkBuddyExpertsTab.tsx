// fork: WorkBuddy expert market tab on the experts page.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Input, Spin } from "antd";
import { RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import cx from "classnames";
import { message } from "@/utils/antdMessage";
import { request } from "../../api/request";
import PillScroller from "../../components/PillScroller";
import { OctopEmptyMascot } from "../../components/EmptyState";
import {
  workbuddyExpertsApi,
  type WbText,
  type WorkBuddyExpertCard,
} from "../../api/modules/workbuddyMarket";
import { apiErrorMessage } from "../../utils/apiError";
import {
  ExpertCard,
  type ExpertSummary,
} from "../Experts/components/ExpertCard";
import styles from "../Experts/index.module.less";
import WorkBuddyExpertDrawer, { expertLabel } from "./WorkBuddyExpertDrawer";
import { useWbText } from "./wbText";

interface Props {
  lang: "zh" | "en";
  installedExpertIds: Set<string>;
  /** Template is ready: open the regular create drawer with these MCP servers. */
  onRequestCreate: (expert: ExpertSummary, mcpServers: string[]) => void;
}

const ALL = "";

export default function WorkBuddyExpertsTab({
  lang,
  installedExpertIds,
  onRequestCreate,
}: Props) {
  const { t } = useTranslation();
  const { tx, pick } = useWbText();
  const [items, setItems] = useState<WorkBuddyExpertCard[]>([]);
  const [categories, setCategories] = useState<(WbText & { id: string })[]>([]);
  const [category, setCategory] = useState(ALL);
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<WorkBuddyExpertCard | null>(null);
  const [creating, setCreating] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError("");
      try {
        const resp = await workbuddyExpertsApi.list(
          {
            q: query || undefined,
            category: category || undefined,
            refresh,
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setItems(resp.items ?? []);
        setCategories(resp.categories ?? []);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(
            apiErrorMessage(
              err,
              tx("WorkBuddy 专家加载失败", "Failed to load WorkBuddy experts"),
              t,
            ),
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [category, query, t, tx],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(keyword.trim()), 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const cards = useMemo(
    () =>
      items.map((item) => ({
        item,
        summary: {
          id: item.template_id,
          label: { zh: expertLabel(item, pick), en: expertLabel(item, pick) },
          description: {
            zh: item.description.zh ?? undefined,
            en: item.description.en ?? undefined,
          },
          icon_url: item.avatar_url,
          icon_name: "sparkles",
          scene: pick(item.category),
        } satisfies ExpertSummary,
      })),
    [items, pick],
  );

  const handleCreate = useCallback(
    async (expert: WorkBuddyExpertCard) => {
      setCreating(true);
      try {
        const prepared = await workbuddyExpertsApi.prepare(expert.plugin);
        const summary = await request<ExpertSummary>(
          `/experts/${encodeURIComponent(prepared.expert_id)}`,
        );
        const unusable = prepared.skipped_mcp.filter(
          (m) => m.reason !== "not_requested",
        );
        if (prepared.mcp_servers.length > 0) {
          message.success(
            tx(
              `已添加 MCP：${prepared.mcp_servers.join("、")}`,
              `MCP added: ${prepared.mcp_servers.join(", ")}`,
            ),
          );
        }
        if (unusable.length > 0) {
          message.warning(
            tx(
              `${unusable.length} 个 MCP 无法使用，已跳过`,
              `${unusable.length} MCP server(s) skipped`,
            ),
          );
        }
        setSelected(null);
        onRequestCreate(summary, prepared.mcp_servers);
      } catch (err) {
        message.error(
          apiErrorMessage(
            err,
            tx("准备专家失败", "Failed to prepare expert"),
            t,
          ),
        );
      } finally {
        setCreating(false);
      }
    },
    [onRequestCreate, t, tx],
  );

  if (loading && items.length === 0 && !error) {
    return (
      <div className={styles.loadingState}>
        <Spin />
      </div>
    );
  }

  return (
    <>
      <div className={styles.gridToolbar}>
        <span className={styles.gridCount}>
          {tx(`共 ${items.length} 个专家`, `${items.length} experts`)}
        </span>
        <div className={styles.gridToolbarRight}>
          <Input
            className={styles.toolbarSearch}
            prefix={<Search size={14} />}
            allowClear
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder={tx("搜索 WorkBuddy 专家", "Search WorkBuddy experts")}
          />
          <button
            className={styles.toolbarIconBtn}
            onClick={() => void load(true)}
            disabled={loading}
            type="button"
            aria-label={t("common.refresh")}
          >
            <RefreshCw size={14} className={cx(loading && styles.spinning)} />
          </button>
        </div>
      </div>
      {categories.length > 0 && (
        <PillScroller
          options={[
            { value: ALL, label: t("experts.sceneAll") },
            ...categories.map((c) => ({ value: c.id, label: pick(c, c.id) })),
          ]}
          active={category}
          onSelect={setCategory}
          prevLabel={t("experts.marketScenePrev")}
          nextLabel={t("experts.marketSceneNext")}
        />
      )}
      <div
        style={{
          fontSize: 12,
          color: "var(--fn-text-tertiary)",
          margin: "4px 0 12px",
        }}
      >
        {tx(
          "内容来自腾讯 WorkBuddy 公开专家中心（仅单人专家），按需下载并缓存在本机；版权归原作者所有。",
          "From Tencent WorkBuddy's public expert center (single experts only), downloaded on demand and cached locally; rights belong to the original authors.",
        )}
      </div>
      {error ? (
        <div className={styles.emptyState}>
          <OctopEmptyMascot />
          <div className={styles.emptyHint}>{error}</div>
          <div className={styles.emptyActions}>
            <button
              className={styles.toolbarBtn}
              onClick={() => void load(true)}
              type="button"
            >
              {t("common.refresh")}
            </button>
          </div>
        </div>
      ) : cards.length === 0 ? (
        <div className={styles.searchEmpty}>{t("experts.searchEmpty")}</div>
      ) : (
        <div className={styles.cardGrid}>
          {cards.map(({ item, summary }) => (
            <ExpertCard
              key={item.plugin}
              expert={summary}
              lang={lang}
              isInstalled={installedExpertIds.has(item.template_id)}
              onCreate={() => setSelected(item)}
            />
          ))}
        </div>
      )}
      <WorkBuddyExpertDrawer
        open={selected !== null}
        expert={selected}
        creating={creating}
        onClose={() => setSelected(null)}
        onCreate={(e) => void handleCreate(e)}
      />
    </>
  );
}
