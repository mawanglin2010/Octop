// fork: preview drawer for a WorkBuddy market skill.
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Drawer, Spin, Tag } from "antd";
import { Download, FileText, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import Markdown from "../../components/Markdown";
import { ExpertIcon } from "../Experts/components/iconForName";
import {
  workbuddyMarketApi,
  type WorkBuddySkillCard,
  type WorkBuddySkillDetail,
} from "../../api/modules/workbuddyMarket";
import { apiErrorMessage } from "../../utils/apiError";
import { formatBytes, stripFrontmatter, useWbText } from "./wbText";

interface Props {
  open: boolean;
  skill: WorkBuddySkillCard | null;
  onClose: () => void;
  /** Omit to show a read-only preview (no install button). */
  onInstall?: (skill: WorkBuddySkillCard) => void;
  installing: boolean;
  isInstalled: boolean;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: "var(--fn-text-tertiary)",
          marginBottom: 10,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

export default function WorkBuddySkillDrawer({
  open,
  skill,
  onClose,
  onInstall,
  installing,
  isInstalled,
}: Props) {
  const { t } = useTranslation();
  const { tx, pick, pickList } = useWbText();
  const [detail, setDetail] = useState<WorkBuddySkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const slug = skill?.slug;

  useEffect(() => {
    if (!open || !slug) return;
    let cancelled = false;
    setDetail(null);
    setError(null);
    workbuddyMarketApi
      .getSkill(slug)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "", t) || String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, slug, t]);

  if (!skill) return null;
  const examples = pickList(skill.examples);
  const tags = pickList(skill.tags);

  return (
    <Drawer
      placement="right"
      open={open}
      onClose={onClose}
      width={600}
      destroyOnHidden
      title={
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            minWidth: 0,
          }}
        >
          <ExpertIcon iconUrl={skill.icon_url} iconName="zap" size={32} />
          <span
            style={{
              fontSize: 16,
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {pick(skill.name, skill.slug)}
          </span>
        </div>
      }
      styles={{ body: { padding: "20px 24px" } }}
      footer={
        onInstall ? (
          <Button
            type={isInstalled ? "default" : "primary"}
            size="large"
            icon={
              isInstalled ? <RefreshCw size={14} /> : <Download size={14} />
            }
            loading={installing}
            disabled={installing}
            onClick={() => onInstall(skill)}
            style={{ width: "100%" }}
          >
            {installing
              ? t("skills.installing")
              : isInstalled
              ? t("skills.reinstall")
              : t("skills.install")}
          </Button>
        ) : null
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <Tag bordered={false} style={{ background: "var(--fn-bg-tertiary)" }}>
            {t("skills.skillVersion")} {skill.version || "—"}
          </Tag>
          <Tag bordered={false} style={{ background: "var(--fn-bg-tertiary)" }}>
            <code>{skill.slug}</code>
          </Tag>
          {isInstalled && <Tag color="green">{t("skills.installed")}</Tag>}
          {skill.featured && <Tag color="gold">{tx("精选", "Featured")}</Tag>}
          {tags.map((tag) => (
            <Tag key={tag} bordered={false}>
              {tag}
            </Tag>
          ))}
          <Tag bordered={false}>
            {tx(`${skill.file_count} 个文件`, `${skill.file_count} files`)} ·{" "}
            {formatBytes(skill.total_bytes)}
          </Tag>
        </div>

        {skill.mentions_workbuddy && (
          <Alert
            type="warning"
            showIcon
            message={tx(
              "说明里提到了 WorkBuddy / CodeBuddy，可能依赖它们的专用工具，在 Octop 里部分功能可能用不了。",
              "Its instructions mention WorkBuddy / CodeBuddy and may rely on their own tools; some steps may not work in Octop.",
            )}
          />
        )}

        <Section title={tx("简介", "About")}>
          <div style={{ lineHeight: 1.7, color: "var(--fn-text-secondary)" }}>
            {pick(skill.summary) ||
              skill.description ||
              t("skills.noDescription")}
          </div>
          {skill.description && pick(skill.summary) && (
            <div
              style={{
                marginTop: 8,
                fontSize: 12,
                lineHeight: 1.6,
                color: "var(--fn-text-tertiary)",
              }}
            >
              {skill.description}
            </div>
          )}
        </Section>

        {examples.length > 0 && (
          <Section title={tx("试试这样问", "Try asking")}>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
              {examples.map((ex) => (
                <li key={ex}>{ex}</li>
              ))}
            </ul>
          </Section>
        )}

        {error ? (
          <Alert type="error" showIcon message={error} />
        ) : !detail ? (
          <div style={{ textAlign: "center", padding: 24 }}>
            <Spin />
          </div>
        ) : (
          <>
            <Section title={tx("文件", "Files")}>
              <div
                style={{
                  maxHeight: 180,
                  overflow: "auto",
                  fontSize: 12,
                  fontFamily: "var(--fn-font-mono, monospace)",
                  background: "var(--fn-bg-tertiary)",
                  borderRadius: 8,
                  padding: "8px 12px",
                }}
              >
                {detail.files.map((f) => (
                  <div
                    key={f.path}
                    style={{
                      display: "flex",
                      gap: 8,
                      justifyContent: "space-between",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-flex",
                        gap: 6,
                        alignItems: "center",
                      }}
                    >
                      <FileText size={12} />
                      {f.path}
                    </span>
                    <span style={{ color: "var(--fn-text-tertiary)" }}>
                      {formatBytes(f.size)}
                    </span>
                  </div>
                ))}
              </div>
            </Section>
            <Section title="SKILL.md">
              <Markdown content={stripFrontmatter(detail.skill_md)} />
            </Section>
          </>
        )}
      </div>
    </Drawer>
  );
}
