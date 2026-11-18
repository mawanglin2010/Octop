// fork: browse / preview / install skills from the WorkBuddy skill market.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Input, Segmented, Spin, Tag } from "antd";
import { AlertTriangle, Download, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { message } from "@/utils/antdMessage";
import { request } from "../../api/request";
import {
  workbuddyMarketApi,
  type WbText,
  type WorkBuddyInstallTarget,
  type WorkBuddySkillCard,
} from "../../api/modules/workbuddyMarket";
import { apiErrorMessage } from "../../utils/apiError";
import { ExpertIcon } from "../Experts/components/iconForName";
import styles from "../Agent/Skills/index.module.less";
import WorkBuddySkillDrawer from "./WorkBuddySkillDrawer";
import { useWbText } from "./wbText";

interface Props {
  /** Install destination; ``null`` shows a read-only browser. */
  target: WorkBuddyInstallTarget | null;
  onInstalled?: () => void;
}

const ALL = "__all__";

function installedListPath(target: WorkBuddyInstallTarget): string {
  return target.type === "agent"
    ? `/agents/${encodeURIComponent(target.agentId)}/skills`
    : `/skill-packages/${encodeURIComponent(target.packageId)}/skills`;
}

export default function WorkBuddySkillsTab({ target, onInstalled }: Props) {
  const { t } = useTranslation();
  const { tx, pick } = useWbText();
  const [items, setItems] = useState<WorkBuddySkillCard[]>([]);
  const [categories, setCategories] = useState<WbText[]>([]);
  const [category, setCategory] = useState<string>(ALL);
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [installed, setInstalled] = useState<Set<string>>(new Set());
  const [installing, setInstalling] = useState<string | null>(null);
  const [selected, setSelected] = useState<WorkBuddySkillCard | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const targetKey = target
    ? target.type === "agent"
      ? `agent:${target.agentId}`
      : `package:${target.packageId}`
    : "none";

  const load = useCallback(
    async (refresh = false) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setLoadError(null);
      try {
        const resp = await workbuddyMarketApi.listSkills(
          {
            q: query || undefined,
            category: category === ALL ? undefined : category,
            refresh,
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setItems(resp.items ?? []);
        setCategories(resp.categories ?? []);
      } catch (err) {
        if (!controller.signal.aborted) {
          setLoadError(
            apiErrorMessage(
              err,
              tx(
                "WorkBuddy 技能市场加载失败",
                "Failed to load the WorkBuddy skill market",
              ),
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

  // Abort only on unmount; a newer load() aborts the older request itself.
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(keyword.trim()), 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  useEffect(() => {
    if (!target) {
      setInstalled(new Set());
      return;
    }
    request<Array<{ name: string; slug?: string }>>(installedListPath(target))
      .then((rows) =>
        setInstalled(new Set((rows ?? []).map((r) => r.slug ?? r.name))),
      )
      .catch(() => {
        // non-critical: installed markers may be stale
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  const handleInstall = useCallback(
    async (skill: WorkBuddySkillCard) => {
      if (!target || installing) return;
      setInstalling(skill.slug);
      try {
        const result = await workbuddyMarketApi.installSkill(
          target,
          skill.slug,
        );
        if (result?.installed) {
          message.success(t("skills.installSuccess"));
          setInstalled((prev) => new Set([...prev, skill.slug]));
          setSelected(null);
          onInstalled?.();
        } else {
          message.error(t("skills.installFailed"));
        }
      } catch (err) {
        message.error(apiErrorMessage(err, t("skills.installFailed"), t));
      } finally {
        setInstalling(null);
      }
    },
    [installing, onInstalled, t, target],
  );

  const categoryOptions = useMemo(
    () => [
      { value: ALL, label: tx("全部", "All") },
      ...categories
        .filter((c) => c.zh)
        .map((c) => ({ value: c.zh as string, label: pick(c) })),
    ],
    [categories, pick, tx],
  );

  const sorted = useMemo(
    () =>
      items
        .slice()
        .sort(
          (a, b) =>
            Number(installed.has(b.slug)) - Number(installed.has(a.slug)),
        ),
    [installed, items],
  );

  return (
    <div className={styles.skillHubContainer}>
      <div className={styles.skillHubToolbar}>
        <Input
          className={styles.skillHubSearch}
          placeholder={tx("搜索 WorkBuddy 技能", "Search WorkBuddy skills")}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          allowClear
          suffix={loading && keyword ? <Spin size="small" /> : undefined}
        />
        <Button
          icon={<RefreshCw size={14} />}
          type="text"
          size="small"
          style={{ color: "var(--fn-text-tertiary)" }}
          onClick={() => void load(true)}
        >
          {t("common.refresh")}
        </Button>
      </div>

      {categoryOptions.length > 1 && (
        <div style={{ overflowX: "auto", marginBottom: 12 }}>
          <Segmented
            value={category}
            onChange={(v) => setCategory(String(v))}
            options={categoryOptions}
          />
        </div>
      )}

      <div
        style={{
          fontSize: 12,
          color: "var(--fn-text-tertiary)",
          marginBottom: 12,
        }}
      >
        {tx(
          "内容来自腾讯 WorkBuddy 公开技能市场，按需下载并缓存在本机；版权归原作者所有。",
          "Content comes from Tencent WorkBuddy's public skill market, downloaded on demand and cached locally; rights belong to the original authors.",
        )}
      </div>

      {loadError ? (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <div
            style={{
              color: "var(--fn-color-danger, #ff4d4f)",
              marginBottom: 16,
            }}
          >
            {loadError}
          </div>
          <Button
            icon={<RefreshCw size={14} />}
            onClick={() => void load(true)}
          >
            {t("common.refresh")}
          </Button>
        </div>
      ) : loading && items.length === 0 ? (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            minHeight: 200,
            alignItems: "center",
          }}
        >
          <Spin size="large" />
        </div>
      ) : sorted.length === 0 ? (
        <div
          style={{
            color: "var(--fn-text-tertiary)",
            textAlign: "center",
            padding: "40px 0",
          }}
        >
          {t("skills.rankingsEmpty")}
        </div>
      ) : (
        <div className={styles.hubGrid}>
          {sorted.map((skill) => {
            const isInstalled = installed.has(skill.slug);
            return (
              <div
                key={skill.slug}
                className={styles.hubCard}
                onClick={() => setSelected(skill)}
              >
                <div className={styles.hubCardHeader}>
                  <ExpertIcon
                    iconUrl={skill.icon_url}
                    iconName="zap"
                    size={24}
                  />
                  <span className={styles.hubCardName}>
                    {pick(skill.name, skill.slug)}
                  </span>
                  {skill.mentions_workbuddy && (
                    <AlertTriangle
                      size={14}
                      style={{
                        color: "var(--fn-color-warning, #faad14)",
                        flexShrink: 0,
                      }}
                      aria-label={tx(
                        "可能依赖 WorkBuddy",
                        "May rely on WorkBuddy",
                      )}
                    />
                  )}
                  {skill.featured && (
                    <Tag color="gold" style={{ margin: 0, flexShrink: 0 }}>
                      {tx("精选", "Featured")}
                    </Tag>
                  )}
                </div>
                <div className={styles.hubCardDesc}>
                  {pick(skill.summary) ||
                    skill.description ||
                    t("skills.noDescription")}
                </div>
                <div className={styles.hubCardFooter}>
                  <span className={styles.hubCardStat}>
                    {pick(skill.category)}
                  </span>
                  {target && (
                    <Button
                      size="small"
                      type={isInstalled ? "default" : "primary"}
                      icon={
                        isInstalled ? (
                          <RefreshCw size={14} />
                        ) : (
                          <Download size={14} />
                        )
                      }
                      loading={installing === skill.slug}
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleInstall(skill);
                      }}
                    >
                      {isInstalled
                        ? t("skills.reinstall")
                        : t("skills.install")}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <WorkBuddySkillDrawer
        open={selected !== null}
        skill={selected}
        onClose={() => setSelected(null)}
        onInstall={target ? (s) => void handleInstall(s) : undefined}
        installing={installing === selected?.slug}
        isInstalled={selected !== null && installed.has(selected.slug)}
      />
    </div>
  );
}
