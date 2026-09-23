import { useCallback, useEffect, useState, type CSSProperties } from "react";
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Collapse,
  Input,
  Modal,
  Popconfirm,
  Table,
  Tag,
} from "antd";
import {
  DeleteOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SyncOutlined,
} from "@ant-design/icons";

import {
  apiMediaEmptyTrash,
  apiMediaHealth,
  apiMediaRestore,
  apiMediaTrash,
  apiRunTask,
  type MediaHealth,
} from "../api/admin";
import { hasPermission, useAuth } from "../auth";
import PaintEmpty from "./PaintEmpty";

/** 字节 → 人话（运营只看"多少 M"）。 */
function fmtBytes(bytes: number): string {
  if (!bytes) return "0 KB";
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

const NUM: CSSProperties = { fontFamily: "var(--font-mono)" };
const MUTED: CSSProperties = { color: "rgba(0,0,0,0.45)" };

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 96 }}>
      <span style={{ ...MUTED, fontSize: 12 }}>{label}</span>
      <span style={{ ...NUM, fontSize: 18, color: tone ?? "inherit" }}>{value}</span>
    </div>
  );
}

export default function MediaHealthPanel() {
  const { message } = AntdApp.useApp();
  const { permissions } = useAuth();
  const isSuper = hasPermission(permissions, "audit.view"); // 与退款中心/仪表盘同一判定（员工管理同款）
  const [health, setHealth] = useState<MediaHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashReason, setTrashReason] = useState("");
  const [emptyOpen, setEmptyOpen] = useState(false);
  const [emptyReason, setEmptyReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await apiMediaHealth());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const recount = async () => {
    setBusy(true);
    try {
      const r = await apiRunTask("media_census");
      if (r.status === "success") {
        message.success(`盘点完成：当前孤儿图 ${r.processed ?? 0} 张`);
      } else {
        message.error(`盘点失败：${r.error ?? ""}`);
      }
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const clean = async () => {
    setBusy(true);
    try {
      const r = await apiMediaTrash(trashReason.trim());
      const skipped = r.skipped?.length ?? 0;
      message.success(
        `已移入回收站 ${r.moved} 张（${fmtBytes(r.moved_bytes)}）` +
          (skipped ? `，跳过 ${skipped} 张（正在使用的图不会动）` : "")
      );
      setTrashOpen(false);
      setTrashReason("");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const emptyTrash = async () => {
    setBusy(true);
    try {
      const r = await apiMediaEmptyTrash(emptyReason.trim());
      message.success(
        `已永久删除 ${r.purged} 张（${fmtBytes(r.purged_bytes)}）` +
          (r.restored > 0 ? `；其中 ${r.restored} 张又被引用，已自动还原回原位` : "")
      );
      setEmptyOpen(false);
      setEmptyReason("");
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const restore = async (id: number, path: string) => {
    setBusy(true);
    try {
      await apiMediaRestore(id);
      message.success(`已还原：${path}`);
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const census = health?.census;
  const trash = health?.trash;
  const trashFiles = trash?.files ?? 0;

  return (
    <Card
      title="媒体体检"
      loading={loading}
      extra={
        <span style={{ display: "inline-flex", gap: 8, flexWrap: "wrap" }}>
          <Button icon={<SyncOutlined />} loading={busy} onClick={() => void recount()}>
            重新点数
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          {isSuper && (
            <>
              <Button
                danger
                type="primary"
                icon={<DeleteOutlined />}
                disabled={!census || census.orphan_files === 0}
                onClick={() => setTrashOpen(true)}
              >
                清理孤儿图片
              </Button>
              <Popconfirm
                title="清空回收站？"
                description="永久删除，不可恢复。正在被使用的图会被自动救回。"
                onConfirm={() => setEmptyOpen(true)}
                okText="继续"
              >
                <Button danger disabled={trashFiles === 0}>
                  清空回收站
                </Button>
              </Popconfirm>
            </>
          )}
        </span>
      }
    >
      {/* 摘要在首屏——明细收进折叠区（用户 2026-09-23：卡片太长把定时任务挤下去了） */}
      {!census ? (
        <PaintEmpty message="还没有盘点记录——点「重新点数」立即生成一份（每天 08:00 也会自动跑一次）" />
      ) : (
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <span style={{ ...MUTED, fontSize: 12 }}>孤儿图片（可清理）</span>
          <span
            style={{
              ...NUM,
              fontSize: 20,
              color: census.orphan_files > 0 ? "#D4380D" : "inherit",
            }}
          >
            {census.orphan_files} 张
          </span>
          <span style={{ ...NUM, fontSize: 16 }}>{fmtBytes(census.orphan_bytes)}</span>
          <span style={{ ...MUTED, fontSize: 12 }}>
            统计时间 {census.created_at} · 回收站 {trashFiles} 张（{fmtBytes(trash?.bytes ?? 0)}）
          </span>
        </div>
      )}

      <Collapse
        ghost
        style={{ marginTop: 8 }}
        items={[
          {
            key: "detail",
            label: `明细与回收站（回收站 ${trashFiles} 张）`,
            children: (
              <>
                {(trash?.unmanaged ?? 0) > 0 && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message={`回收站里有 ${trash?.unmanaged} 个文件没有记账（通常是重建演示库时被清掉了账）：点「重新点数」会自动接管它们，文件一个都不会删`}
                  />
                )}
                {census ? (
                  <>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 32, marginBottom: 12 }}>
                      <Figure label="uploads 文件总数" value={String(census.files_total)} />
                      <Figure label="DB 引用" value={String(census.referenced_total)} />
                      <Figure label="保护目录跳过" value={String(census.protected_total)} />
                      <Figure
                        label="引用悬空"
                        value={String(census.missing_refs)}
                        tone={census.missing_refs > 0 ? "#D4380D" : undefined}
                      />
                    </div>
                    {census.missing_refs > 0 && (
                      <Alert
                        type="warning"
                        showIcon
                        style={{ marginBottom: 12 }}
                        message={`有 ${census.missing_refs} 个被引用的文件在磁盘上不存在（DB 里有记录、文件丢了）——这不是清理造成的，属需要排查的异常`}
                      />
                    )}
                    <Table
                      rowKey="bucket"
                      size="small"
                      pagination={false}
                      style={{ marginBottom: 12 }}
                      dataSource={census.breakdown ?? []}
                      locale={{ emptyText: <PaintEmpty message="当前没有孤儿图" /> }}
                      columns={[
                        { title: "目录", dataIndex: "bucket", width: 140 },
                        {
                          title: "孤儿张数",
                          dataIndex: "files",
                          width: 100,
                          render: (v: number) => <span style={NUM}>{v}</span>,
                        },
                        {
                          title: "占用",
                          dataIndex: "bytes",
                          width: 110,
                          render: (v: number) => <span style={NUM}>{fmtBytes(v)}</span>,
                        },
                      ]}
                    />
                  </>
                ) : null}

                <Table
                  rowKey="id"
                  size="small"
                  pagination={false}
                  dataSource={trash?.items ?? []}
                  locale={{ emptyText: <PaintEmpty message="回收站为空" /> }}
                  columns={[
                    {
                      title: "回收站（移入时间倒序，最多 50 条）",
                      dataIndex: "rel_path",
                      ellipsis: true,
                    },
                    {
                      title: "大小",
                      dataIndex: "bytes",
                      width: 90,
                      render: (v: number) => <span style={NUM}>{fmtBytes(v)}</span>,
                    },
                    { title: "移入时间", dataIndex: "created_at", width: 140 },
                    {
                      title: "保留至",
                      dataIndex: "restore_until",
                      width: 110,
                      render: (v: string) => v || <span style={MUTED}>—</span>,
                    },
                    {
                      title: "操作人",
                      dataIndex: "actor_name",
                      width: 100,
                      render: (v: string) => v || <span style={MUTED}>—</span>,
                    },
                    {
                      title: "操作",
                      width: 100,
                      render: (_: unknown, row: { id: number; rel_path: string }) =>
                        isSuper ? (
                          <Popconfirm
                            title="还原这张图？"
                            description="文件会回到原来的位置，重新计入孤儿统计。"
                            onConfirm={() => void restore(row.id, row.rel_path)}
                          >
                            <Button size="small" icon={<RollbackOutlined />} loading={busy}>
                              还原
                            </Button>
                          </Popconfirm>
                        ) : (
                          <span style={MUTED}>—</span>
                        ),
                    },
                  ]}
                />

                <div style={{ ...MUTED, fontSize: 12, marginTop: 12 }}>
                  清理只动<b>可再生目录</b>（cover / circle / reports / book_audio），且会当场重新核对一遍数据库引用：
                  正在使用的图、收款凭证、评估报告图、活动配图一律不动；清掉的图先进回收站
                  （保留 {trash?.retain_days ?? 30} 天，可随时还原；「清空回收站」= 立即永久删除，
                  但同样会先核对引用——<b>又被用上的图会被自动救回</b>）。
                  {!isSuper && "（清理、还原、清空回收站仅超级管理员可操作）"}
                </div>
              </>
            ),
          },
        ]}
      />

      <Modal
        open={trashOpen}
        title="把当前全部孤儿图移入回收站？"
        okText="确认清理"
        okButtonProps={{ danger: true, loading: busy }}
        onOk={() => void clean()}
        onCancel={() => setTrashOpen(false)}
      >
        <p style={{ marginTop: 0 }}>
          将清理 <b>{census?.orphan_files ?? 0} 张</b>孤儿图（{fmtBytes(census?.orphan_bytes ?? 0)}）。
          文件只是<b>移动</b>到回收站，不会直接删除，必要时可以还原。
        </p>
        <p style={{ ...MUTED, fontSize: 12 }}>
          正在使用的图片会被系统自动跳过（以此刻数据库的引用为准）。
        </p>
        <Input.TextArea
          rows={2}
          value={trashReason}
          maxLength={200}
          showCount
          placeholder="清理原因（必填，写入审计日志）"
          onChange={(e) => setTrashReason(e.target.value)}
        />
        <p style={{ marginBottom: 0, marginTop: 8 }}>
          <Tag color="blue">仅超级管理员</Tag>
        </p>
      </Modal>

      <Modal
        open={emptyOpen}
        title="清空回收站（永久删除）？"
        okText="永久删除"
        okButtonProps={{ danger: true, loading: busy }}
        onOk={() => void emptyTrash()}
        onCancel={() => setEmptyOpen(false)}
      >
        <p style={{ marginTop: 0 }}>
          回收站里有 <b>{trashFiles} 张</b>（{fmtBytes(trash?.bytes ?? 0)}），清空后
          <b>不可恢复</b>，也不再有「还原」入口。
        </p>
        <p style={{ ...MUTED, fontSize: 12 }}>
          删除前仍会核对一遍数据库引用：<b>期间又被用上的图会被自动救回原位</b>，只有确认无人使用的才会被删除。
          若只是想省空间、不急着删，可以什么都不做——它们会在满 {trash?.retain_days ?? 30} 天后自动清除。
        </p>
        <Input.TextArea
          rows={2}
          value={emptyReason}
          maxLength={200}
          showCount
          placeholder="清空原因（必填，写入审计日志）"
          onChange={(e) => setEmptyReason(e.target.value)}
        />
        <p style={{ marginBottom: 0, marginTop: 8 }}>
          <Tag color="red">永久删除不可恢复</Tag>
          <Tag color="blue">仅超级管理员</Tag>
        </p>
      </Modal>
    </Card>
  );
}
