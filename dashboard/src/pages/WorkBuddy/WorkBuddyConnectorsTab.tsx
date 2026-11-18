// fork: install WorkBuddy market connectors as custom MCP servers (admin only).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Form,
  Input,
  Spin,
  Switch,
  Tag,
} from "antd";
import { Download, RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { message } from "@/utils/antdMessage";
import {
  workbuddyConnectorsApi,
  type WorkBuddyConnectorCard,
  type WorkBuddyConnectorDetail,
  type WorkBuddyConnectorStatus,
} from "../../api/modules/workbuddyMarket";
import { apiErrorMessage } from "../../utils/apiError";
import { ExpertIcon } from "../Experts/components/iconForName";
import styles from "../Agent/Skills/index.module.less";
import { useWbText } from "./wbText";

interface Props {
  /** Called with the custom MCP server name after a successful install. */
  onInstalled: (serverName: string) => void;
}

function useStatusText() {
  const { tx } = useWbText();
  return useCallback(
    (status: WorkBuddyConnectorStatus): string =>
      ({
        supported: tx("可安装", "Installable"),
        cli: tx("命令行工具型，不支持", "CLI-based; not supported"),
        stdio: tx(
          "需在服务器运行程序，暂不支持",
          "Local process; not supported yet",
        ),
        sse: tx("SSE 方式暂不支持", "SSE not supported"),
        server_side: tx(
          "凭证由 WorkBuddy 云端代管，无法在 Octop 使用",
          "Credentials held by WorkBuddy's cloud; unusable here",
        ),
        internal: tx("内网服务，无法使用", "Intranet only"),
        no_mcp: tx("没有 MCP 服务", "No MCP server"),
        unsupported_auth: tx("认证方式不支持", "Unsupported auth"),
      })[status] ?? status,
    [tx],
  );
}

function ConnectorDrawer({
  card,
  onClose,
  onInstalled,
}: {
  card: WorkBuddyConnectorCard | null;
  onClose: () => void;
  onInstalled: (serverName: string, auth: string) => void;
}) {
  const { t } = useTranslation();
  const { tx, pick, pickList } = useWbText();
  const statusText = useStatusText();
  const [detail, setDetail] = useState<WorkBuddyConnectorDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shared, setShared] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [form] = Form.useForm<Record<string, string>>();
  const id = card?.id;

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setDetail(null);
    setError(null);
    setShared(false);
    form.resetFields();
    workbuddyConnectorsApi
      .get(id)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setError(apiErrorMessage(err, "", t) || String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [id, form, t]);

  const install = useCallback(async () => {
    if (!detail) return;
    let values: Record<string, string> = {};
    if (detail.auth === "token") {
      try {
        values = await form.validateFields();
      } catch {
        return;
      }
    }
    setInstalling(true);
    try {
      const result = await workbuddyConnectorsApi.install(
        detail.id,
        values,
        shared,
      );
      message.success(
        detail.auth === "oauth"
          ? tx(
              "已添加，请在「自定义」里完成授权",
              "Added — finish authorization under Custom",
            )
          : tx("已添加为自定义 MCP", "Added as a custom MCP server"),
      );
      onInstalled(result.servers[0] ?? "", result.auth);
    } catch (err) {
      message.error(apiErrorMessage(err, tx("安装失败", "Install failed"), t));
    } finally {
      setInstalling(false);
    }
  }, [detail, form, onInstalled, shared, t, tx]);

  if (!card) return null;
  const examples = pickList(card.examples);
  const supported = card.status === "supported";

  return (
    <Drawer
      open
      onClose={onClose}
      width={560}
      destroyOnHidden
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <ExpertIcon iconUrl={card.icon_url} iconName="network" size={32} />
          <span style={{ fontWeight: 600 }}>{pick(card.name, card.id)}</span>
        </div>
      }
      styles={{ body: { padding: "20px 24px" } }}
      footer={
        supported ? (
          <Button
            type="primary"
            size="large"
            icon={<Download size={14} />}
            loading={installing}
            disabled={!detail}
            onClick={() => void install()}
            style={{ width: "100%" }}
          >
            {card.installed
              ? tx("重新安装（更新凭证）", "Reinstall (update credentials)")
              : tx("安装为自定义 MCP", "Install as custom MCP")}
          </Button>
        ) : null
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Tag color={supported ? "green" : "default"}>
            {statusText(card.status)}
          </Tag>
          {supported && card.auth === "token" && (
            <Tag color="orange">{tx("需要 API Key", "Needs an API key")}</Tag>
          )}
          {supported && card.auth === "oauth" && (
            <Tag color="blue">
              {tx("安装后需授权", "Authorize after install")}
            </Tag>
          )}
          {card.installed && <Tag color="green">{t("skills.installed")}</Tag>}
        </div>
        <div style={{ lineHeight: 1.7, color: "var(--fn-text-secondary)" }}>
          {pick(card.description) || "—"}
        </div>
        {examples.length > 0 && (
          <div>
            <div
              style={{
                fontSize: 12,
                color: "var(--fn-text-tertiary)",
                marginBottom: 8,
              }}
            >
              {tx("试试这样问", "Try asking")}
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
              {examples.map((ex) => (
                <li key={ex}>{ex}</li>
              ))}
            </ul>
          </div>
        )}
        {error ? (
          <Alert type="error" showIcon message={error} />
        ) : !detail ? (
          <div style={{ textAlign: "center", padding: 24 }}>
            <Spin />
          </div>
        ) : (
          <>
            {detail.servers.length > 0 && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--fn-text-tertiary)",
                  wordBreak: "break-all",
                }}
              >
                {detail.servers.map((s) => (
                  <div key={s.name}>
                    <code>{s.name}</code> {s.url}
                  </div>
                ))}
              </div>
            )}
            {supported && detail.auth === "token" && (
              <div>
                {pick(detail.form.description ?? null) && (
                  <div
                    style={{
                      fontSize: 13,
                      marginBottom: 12,
                      color: "var(--fn-text-secondary)",
                    }}
                  >
                    {pick(detail.form.description ?? null)}
                    {detail.form.doc_url && (
                      <>
                        {" "}
                        <a
                          href={detail.form.doc_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {pick(detail.form.doc_label ?? null) ||
                            tx("如何获取？", "How to get it?")}
                        </a>
                      </>
                    )}
                  </div>
                )}
                <Form form={form} layout="vertical" requiredMark>
                  {detail.fields.map((f) => (
                    <Form.Item
                      key={f.key}
                      name={f.key}
                      label={pick(f.label, f.key)}
                      extra={pick(f.description) || undefined}
                      rules={
                        f.required
                          ? [
                              {
                                required: true,
                                whitespace: true,
                                message: tx("必填", "Required"),
                              },
                            ]
                          : undefined
                      }
                    >
                      {f.secret ? (
                        <Input.Password
                          placeholder={pick(f.placeholder)}
                          autoComplete="off"
                        />
                      ) : (
                        <Input
                          placeholder={pick(f.placeholder)}
                          autoComplete="off"
                        />
                      )}
                    </Form.Item>
                  ))}
                </Form>
              </div>
            )}
            {supported && (
              <Checkbox
                checked={shared}
                onChange={(e) => setShared(e.target.checked)}
              >
                {tx(
                  "共享给所有用户（凭证由你提供，其他用户可直接使用）",
                  "Share with all users (they use your credentials)",
                )}
              </Checkbox>
            )}
            {detail.skills.length > 0 && (
              <div style={{ fontSize: 12, color: "var(--fn-text-tertiary)" }}>
                {tx(
                  "附带使用技能（未安装）：",
                  "Bundled usage skills (not installed): ",
                )}
                {detail.skills.join("、")}
              </div>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}

export default function WorkBuddyConnectorsTab({ onInstalled }: Props) {
  const { t } = useTranslation();
  const { tx, pick } = useWbText();
  const statusText = useStatusText();
  const [items, setItems] = useState<WorkBuddyConnectorCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [supportedOnly, setSupportedOnly] = useState(true);
  const [selected, setSelected] = useState<WorkBuddyConnectorCard | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      try {
        const resp = await workbuddyConnectorsApi.list(
          { q: query || undefined, supportedOnly, refresh },
          controller.signal,
        );
        if (!controller.signal.aborted) setItems(resp.items ?? []);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(
            apiErrorMessage(
              err,
              tx(
                "WorkBuddy 连接器加载失败",
                "Failed to load WorkBuddy connectors",
              ),
              t,
            ),
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    },
    [query, supportedOnly, t, tx],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(keyword.trim()), 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const counts = useMemo(
    () => ({
      total: items.length,
      installed: items.filter((i) => i.installed).length,
    }),
    [items],
  );

  return (
    <div className={styles.skillHubContainer}>
      <div className={styles.skillHubToolbar}>
        <Input
          className={styles.skillHubSearch}
          prefix={<Search size={14} />}
          placeholder={tx(
            "搜索 WorkBuddy 连接器",
            "Search WorkBuddy connectors",
          )}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          allowClear
        />
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 13,
          }}
        >
          <Switch
            size="small"
            checked={supportedOnly}
            onChange={setSupportedOnly}
          />
          {tx("只看可安装", "Installable only")}
        </span>
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
      <div
        style={{
          fontSize: 12,
          color: "var(--fn-text-tertiary)",
          marginBottom: 12,
        }}
      >
        {tx(
          `共 ${counts.total} 个，已安装 ${counts.installed} 个。远程 MCP 连接器装进你的「自定义」连接器；内容来自腾讯 WorkBuddy 公开连接器市场。`,
          `${counts.total} connectors, ${counts.installed} installed. Remote MCP connectors go into your Custom connectors; listing from Tencent WorkBuddy's public connector market.`,
        )}
      </div>
      {error ? (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <div
            style={{
              color: "var(--fn-color-danger, #ff4d4f)",
              marginBottom: 16,
            }}
          >
            {error}
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
      ) : (
        <div className={styles.hubGrid}>
          {items.map((item) => (
            <div
              key={item.id}
              className={styles.hubCard}
              onClick={() => setSelected(item)}
            >
              <div className={styles.hubCardHeader}>
                <ExpertIcon
                  iconUrl={item.icon_url}
                  iconName="network"
                  size={24}
                />
                <span className={styles.hubCardName}>
                  {pick(item.name, item.id)}
                </span>
                {item.installed && (
                  <Tag color="green" style={{ margin: 0, flexShrink: 0 }}>
                    {t("skills.installed")}
                  </Tag>
                )}
              </div>
              <div className={styles.hubCardDesc}>
                {pick(item.description) || "—"}
              </div>
              <div className={styles.hubCardFooter}>
                <span className={styles.hubCardStat}>
                  {item.status !== "supported"
                    ? statusText(item.status)
                    : item.auth === "token"
                    ? tx("需要 API Key", "Needs an API key")
                    : item.auth === "oauth"
                    ? tx("安装后需授权", "Authorize after install")
                    : tx("免配置", "No setup")}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConnectorDrawer
        card={selected}
        onClose={() => setSelected(null)}
        onInstalled={(serverName) => {
          setSelected(null);
          void load();
          onInstalled(serverName);
        }}
      />
    </div>
  );
}
