// fork: preview a WorkBuddy market expert and turn it into an Octop template.
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Collapse, Drawer, Spin, Tag } from "antd";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import Markdown from "../../components/Markdown";
import { ExpertIcon } from "../Experts/components/iconForName";
import {
  workbuddyExpertsApi,
  type WorkBuddyExpertCard,
  type WorkBuddyExpertDetail,
  type WorkBuddyMcpStatus,
} from "../../api/modules/workbuddyMarket";
import { apiErrorMessage } from "../../utils/apiError";
import { useWbText } from "./wbText";

interface Props {
  open: boolean;
  expert: WorkBuddyExpertCard | null;
  creating: boolean;
  onClose: () => void;
  onCreate: (expert: WorkBuddyExpertCard) => void;
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

export function expertLabel(
  expert: WorkBuddyExpertCard,
  pick: (v: WorkBuddyExpertCard["profession"], f?: string) => string,
): string {
  const profession = pick(expert.profession, expert.plugin);
  const nickname = pick(expert.nickname);
  return nickname && !profession.includes(nickname)
    ? `${profession}·${nickname}`
    : profession;
}

export default function WorkBuddyExpertDrawer({
  open,
  expert,
  creating,
  onClose,
  onCreate,
}: Props) {
  const { t } = useTranslation();
  const { tx, pick, pickList } = useWbText();
  const [detail, setDetail] = useState<WorkBuddyExpertDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const plugin = expert?.plugin;

  useEffect(() => {
    if (!open || !plugin) return;
    let cancelled = false;
    setDetail(null);
    setError(null);
    workbuddyExpertsApi
      .get(plugin)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "", t) || String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, plugin, t]);

  if (!expert) return null;

  const mcpStatusText: Record<WorkBuddyMcpStatus, [string, string]> = {
    ok: [
      tx("创建时自动添加为自定义 MCP", "Added as a custom MCP server"),
      "green",
    ],
    internal: [tx("腾讯内网服务，无法使用", "Tencent intranet only"), "red"],
    stdio: [
      tx(
        "需要在服务器上运行脚本，暂不支持",
        "Runs a local script; not supported yet",
      ),
      "orange",
    ],
    sse: [tx("SSE 方式暂不支持", "SSE transport not supported"), "orange"],
    needs_secret: [
      tx("需要凭证，暂不支持", "Needs credentials; not supported yet"),
      "orange",
    ],
    invalid: [tx("配置无效", "Invalid config"), "default"],
  };
  const examples = pickList(expert.quick_prompts);
  const tags = pickList(expert.tags);

  return (
    <Drawer
      placement="right"
      open={open}
      onClose={onClose}
      width={620}
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
          <ExpertIcon
            iconUrl={expert.avatar_url}
            iconName="sparkles"
            size={36}
          />
          <span
            style={{
              fontSize: 16,
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {expertLabel(expert, pick)}
          </span>
        </div>
      }
      styles={{ body: { padding: "20px 24px" } }}
      footer={
        <Button
          type="primary"
          size="large"
          icon={<Plus size={14} />}
          loading={creating}
          disabled={creating || !!error}
          onClick={() => onCreate(expert)}
          style={{ width: "100%" }}
        >
          {tx("用这个专家创建", "Create from this expert")}
        </Button>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {pick(expert.category) && (
            <Tag bordered={false} color="blue">
              {pick(expert.category)}
            </Tag>
          )}
          {tags.map((tag) => (
            <Tag key={tag} bordered={false}>
              {tag}
            </Tag>
          ))}
          {expert.author && (
            <Tag
              bordered={false}
              style={{ background: "var(--fn-bg-tertiary)" }}
            >
              {tx("作者", "By")} {expert.author}
            </Tag>
          )}
        </div>

        <Section title={tx("简介", "About")}>
          <div style={{ lineHeight: 1.7, color: "var(--fn-text-secondary)" }}>
            {pick(expert.description) || "—"}
          </div>
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
            {detail.connectors.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={tx(
                  `这个专家依赖 WorkBuddy 连接器：${detail.connectors.join(
                    "、",
                  )}。Octop 里没有对应连接器时，相关功能用不了。`,
                  `This expert expects WorkBuddy connectors: ${detail.connectors.join(
                    ", ",
                  )}. Those steps will not work without them.`,
                )}
              />
            )}

            <Section
              title={tx(
                `技能（${detail.skills.length}）`,
                `Skills (${detail.skills.length})`,
              )}
            >
              {detail.skills.length === 0 ? (
                <div style={{ color: "var(--fn-text-tertiary)" }}>—</div>
              ) : (
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 8 }}
                >
                  {detail.skills.map((s) => (
                    <div key={s.name} style={{ lineHeight: 1.5 }}>
                      <code>{s.name}</code>
                      <span
                        style={{
                          marginLeft: 8,
                          fontSize: 12,
                          color: "var(--fn-text-tertiary)",
                        }}
                      >
                        {s.description}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {detail.mcp.length > 0 && (
              <Section title="MCP">
                <div
                  style={{ display: "flex", flexDirection: "column", gap: 8 }}
                >
                  {detail.mcp.map((m) => {
                    const [text, color] = mcpStatusText[m.status] ?? [
                      m.status,
                      "default",
                    ];
                    return (
                      <div
                        key={m.name}
                        style={{
                          display: "flex",
                          gap: 8,
                          alignItems: "center",
                          flexWrap: "wrap",
                        }}
                      >
                        <code>{m.name}</code>
                        <Tag color={color} style={{ margin: 0 }}>
                          {text}
                        </Tag>
                        <span
                          style={{
                            fontSize: 12,
                            color: "var(--fn-text-tertiary)",
                            wordBreak: "break-all",
                          }}
                        >
                          {m.target}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </Section>
            )}

            <Collapse
              size="small"
              items={[
                {
                  key: "prompt",
                  label: tx("提示词（SOUL.md）", "Prompt (SOUL.md)"),
                  children: <Markdown content={detail.prompt} />,
                },
              ]}
            />
          </>
        )}
      </div>
    </Drawer>
  );
}
