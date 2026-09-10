import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Input,
  Modal,
  Select,
  Table,
  Tabs,
  Tag,
  Typography,
  App as AntdApp,
} from "antd";
import { LikeOutlined, LikeFilled, PushpinFilled, PushpinOutlined } from "@ant-design/icons";
import type { Dayjs } from "dayjs";

import PaintEmpty from "../components/PaintEmpty";
import PaintPagination from "../components/PaintPagination";
import { usePaintPagination } from "../hooks/usePaintPagination";
import { TODO_REFRESH_EVENT, useTodoCounts } from "../hooks/useTodoCounts";
import {
  CirclePostItem,
  apiCircleAdminLike,
  apiCircleAdminUnlike,
  apiCircleDeletePost,
  apiCircleOverview,
  apiCirclePin,
  apiCircleUnpin,
  apiListCirclePosts,
  circlePostImageUrl,
} from "../api/circle";

// 类型枚举中英文映射全覆盖（防「英文裸输出」）；「全部」显式首项——A4 三犯纪律
const CARD_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "全部" },
  { value: "milestone", label: "里程碑" },
  { value: "books_count", label: "读本数" },
  { value: "level_up", label: "等级晋级" },
  { value: "perfect_quiz", label: "测验满分" },
  { value: "streak", label: "连续打卡" },
  { value: "finish_book", label: "完读" },
];

function typeTagColor(t: string): string {
  switch (t) {
    case "milestone": return "gold";
    case "books_count": return "green";
    case "level_up": return "blue";
    case "perfect_quiz": return "magenta";
    case "streak": return "cyan";
    default: return "orange";
  }
}

export default function CircleManage() {
  const { message } = AntdApp.useApp();
  const { page, setPage, pageSize, setPageSize } = usePaintPagination(10, 1);
  const { counts: todoCounts, failed } = useTodoCounts();
  const unlikedCount = failed || !todoCounts ? 0 : (todoCounts.circle_unliked ?? 0);

  const [items, setItems] = useState<CirclePostItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cardType, setCardType] = useState<string>("");
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [keyword, setKeyword] = useState("");
  // 运营概览
  const [overview, setOverview] = useState<{
    week_new_posts: number;
    sharing_parents: number;
    total_likes: number;
    admin_liked_coverage: number;
    card_type_distribution: Record<string, number>;
  } | null>(null);
  // 删除 Modal（必填原因——审计留痕）
  const [deleteTarget, setDeleteTarget] = useState<CirclePostItem | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  // 卡片图点击放大（媒体消费点清单：管理端缩略图 + 点击放大，均需 getToken 拼 ?token=）
  const [previewPost, setPreviewPost] = useState<CirclePostItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: Parameters<typeof apiListCirclePosts>[0] = {
        page,
        page_size: pageSize,
      };
      if (cardType) params.card_type = cardType;
      if (range && range[0]) params.start = range[0].format("YYYY-MM-DD HH:mm:ss");
      if (range && range[1]) params.end = range[1].format("YYYY-MM-DD HH:mm:ss");
      if (keyword.trim()) params.keyword = keyword.trim();
      const data = await apiListCirclePosts(params);
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, cardType, range, keyword, message]);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await apiCircleOverview());
    } catch (e) {
      message.error((e as Error).message);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const refreshAfterAction = useCallback(() => {
    void load();
    void loadOverview();
    window.dispatchEvent(new Event(TODO_REFRESH_EVENT));
  }, [load, loadOverview]);

  const toggleAdminLike = useCallback(
    async (r: CirclePostItem) => {
      try {
        if (r.admin_liked) await apiCircleAdminUnlike(r.id);
        else await apiCircleAdminLike(r.id);
        refreshAfterAction();
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [refreshAfterAction, message],
  );

  const togglePin = useCallback(
    async (r: CirclePostItem) => {
      try {
        if (r.is_pinned) {
          await apiCircleUnpin(r.id);
        } else {
          await apiCirclePin(r.id);
          message.success("已置顶（原置顶帖自动取消，同时最多 1 条）");
        }
        refreshAfterAction();
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [refreshAfterAction, message],
  );

  const submitDelete = useCallback(async () => {
    if (!deleteTarget) return;
    if (!deleteReason.trim()) {
      message.error("必须填写删除原因（审计留痕）");
      return;
    }
    try {
      await apiCircleDeletePost(deleteTarget.id, deleteReason.trim());
      message.success("已删除");
      setDeleteTarget(null);
      setDeleteReason("");
      refreshAfterAction();
    } catch (e) {
      message.error((e as Error).message);
    }
  }, [deleteTarget, deleteReason, refreshAfterAction, message]);

  const columns = useMemo(
    () => [
      {
        title: "卡片",
        dataIndex: "id",
        width: 110,
        render: (_: unknown, r: CirclePostItem) => (
          <img
            src={circlePostImageUrl(r.id)}
            alt={r.card_type_label}
            onClick={() => setPreviewPost(r)}
            style={{
              width: 72,
              height: 96,
              objectFit: "cover",
              borderRadius: 6,
              background: "#f5f2ea",
              cursor: "zoom-in",
            }}
          />
        ),
      },
      {
        title: "双署名（家长 · 孩子）",
        key: "dual_name",
        width: 200,
        render: (_: unknown, r: CirclePostItem) => (
          <span>
            {r.parent_name} · {r.child_name}
          </span>
        ),
      },
      {
        title: "类型",
        dataIndex: "card_type",
        width: 110,
        render: (_: unknown, r: CirclePostItem) => (
          <Tag color={typeTagColor(r.card_type)}>{r.card_type_label}</Tag>
        ),
      },
      { title: "点赞数", dataIndex: "like_count", width: 90 },
      {
        title: "馆长赞",
        dataIndex: "admin_liked",
        width: 90,
        render: (v: boolean) =>
          v ? <Tag color="gold">🌟 已赞</Tag> : <Tag bordered={false}>未赞</Tag>,
      },
      {
        title: "置顶",
        dataIndex: "is_pinned",
        width: 80,
        render: (v: boolean) =>
          v ? <Tag color="red">置顶</Tag> : <span style={{ color: "rgba(0,0,0,0.4)" }}>—</span>,
      },
      { title: "发布时间", dataIndex: "created_at", width: 150 },
      {
        title: "操作",
        key: "action",
        width: 250,
        render: (_: unknown, r: CirclePostItem) => (
          <span style={{ display: "inline-flex", gap: 4 }}>
            <Button
              size="small"
              type={r.admin_liked ? "primary" : "default"}
              icon={r.admin_liked ? <LikeFilled /> : <LikeOutlined />}
              onClick={() => void toggleAdminLike(r)}
            >
              {r.admin_liked ? "取消赞" : "馆长赞"}
            </Button>
            <Button
              size="small"
              icon={r.is_pinned ? <PushpinFilled /> : <PushpinOutlined />}
              onClick={() => void togglePin(r)}
            >
              {r.is_pinned ? "取消置顶" : "置顶"}
            </Button>
            <Button size="small" danger onClick={() => setDeleteTarget(r)}>
              删除
            </Button>
          </span>
        ),
      },
    ],
    [toggleAdminLike, togglePin],
  );

  const postsPanel = (
    <>
      {/* 页顶胶囊：今日新帖未赞数（与侧边栏徽标同源） */}
      <div style={{ marginBottom: 12 }}>
        {unlikedCount > 0 ? (
          <span
            style={{
              background: "#ff4d4f",
              color: "#fff",
              borderRadius: 10,
              padding: "2px 10px",
              fontSize: 13,
            }}
          >
            今日新帖 {unlikedCount} 条待赞
          </span>
        ) : (
          <Typography.Text type="secondary">今日新帖均已馆长赞 ✓</Typography.Text>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <Select
          value={cardType}
          style={{ width: 140 }}
          onChange={(v) => {
            setCardType(v);
            setPage(1);
          }}
          options={CARD_TYPE_OPTIONS}
        />
        <DatePicker.RangePicker
          showTime={{ format: "HH:mm" }}
          format="YYYY-MM-DD HH:mm"
          value={range as [Dayjs | null, Dayjs | null] | null}
          onChange={(v) => {
            setRange(v as [Dayjs | null, Dayjs | null] | null);
            setPage(1);
          }}
        />
        <Input.Search
          allowClear
          placeholder="按家长/孩子姓名搜索"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => setPage(1)}
        />
      </div>
      <Table<CirclePostItem>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <PaintEmpty message="还没有帖子——等家长来晒第一个成就" /> }}
        pagination={false}
        columns={columns}
        scroll={{ x: "max-content" }}
      />
      <div style={{ marginTop: 16, textAlign: "right" }}>
        <PaintPagination
          current={page}
          total={total}
          pageSize={pageSize}
          onChange={(p, ps) => {
            setPage(p);
            if (ps && ps !== pageSize) setPageSize(ps);
          }}
        />
      </div>
    </>
  );

  // Tab2 运营概览（不引图表库：统计卡片 + 简单条形分布）
  const maxDist = overview
    ? Math.max(1, ...Object.values(overview.card_type_distribution || {}))
    : 1;
  const overviewPanel = (
    <div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 24 }}>
        <Card size="small" style={{ minWidth: 160 }}>
          <Typography.Text type="secondary">本周新帖数</Typography.Text>
          <div style={{ fontSize: 30, fontWeight: 700, color: "#FF6B35" }}>
            {overview?.week_new_posts ?? "—"}
          </div>
        </Card>
        <Card size="small" style={{ minWidth: 160 }}>
          <Typography.Text type="secondary">分享家长数</Typography.Text>
          <div style={{ fontSize: 30, fontWeight: 700, color: "#60A5FA" }}>
            {overview?.sharing_parents ?? "—"}
          </div>
        </Card>
        <Card size="small" style={{ minWidth: 160 }}>
          <Typography.Text type="secondary">点赞总数</Typography.Text>
          <div style={{ fontSize: 30, fontWeight: 700, color: "#4ADE80" }}>
            {overview?.total_likes ?? "—"}
          </div>
        </Card>
        <Card size="small" style={{ minWidth: 200 }}>
          <Typography.Text type="secondary">馆长赞覆盖率（目标 100%）</Typography.Text>
          <div
            style={{
              fontSize: 30,
              fontWeight: 700,
              color: (overview?.admin_liked_coverage ?? 0) >= 100 ? "#4ADE80" : "#FCD34D",
            }}
          >
            {overview ? `${overview.admin_liked_coverage}%` : "—"}
          </div>
        </Card>
      </div>
      <Card size="small" title="卡片类型分布（反哺模板迭代）">
        {overview && Object.keys(overview.card_type_distribution).length ? (
          Object.entries(overview.card_type_distribution).map(([label, count]) => (
            <div
              key={label}
              style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}
            >
              <span style={{ width: 90, textAlign: "right" }}>{label}</span>
              <div
                style={{
                  height: 18,
                  borderRadius: 9,
                  background: "#FF6B35",
                  width: `${(100 * count) / maxDist}%`,
                  minWidth: 8,
                  transition: "width .3s",
                }}
              />
              <span style={{ color: "rgba(0,0,0,0.65)" }}>{count} 帖</span>
            </div>
          ))
        ) : (
          <PaintEmpty message="暂无帖子数据" />
        )}
      </Card>
    </div>
  );

  return (
    <Card
      title="阅读圈"
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          零 UGC 成就分享 · 卡片全部系统生成
        </Typography.Text>
      }
    >
      <Tabs
        defaultActiveKey="posts"
        items={[
          { key: "posts", label: "帖子管理", children: postsPanel },
          { key: "overview", label: "运营概览", children: overviewPanel },
        ]}
      />
      <Modal
        title="删除帖子（必填原因，审计留痕）"
        open={deleteTarget !== null}
        onOk={() => void submitDelete()}
        onCancel={() => setDeleteTarget(null)}
        okText="确认删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <div style={{ marginBottom: 8, color: "rgba(0,0,0,0.65)" }}>
          {deleteTarget?.parent_name} · {deleteTarget?.child_name} ·{" "}
          {deleteTarget?.card_type_label}
        </div>
        <Input.TextArea
          rows={3}
          maxLength={200}
          showCount
          placeholder="必填：删除原因（如内容不适宜，将写入审计日志）"
          value={deleteReason}
          onChange={(e) => setDeleteReason(e.target.value)}
        />
      </Modal>
      <Modal
        title="成就卡片预览"
        open={previewPost !== null}
        onCancel={() => setPreviewPost(null)}
        footer={null}
        width={560}
      >
        {previewPost && (
          <>
            <div style={{ marginBottom: 8, color: "rgba(0,0,0,0.65)" }}>
              {previewPost.parent_name} · {previewPost.child_name} · {previewPost.card_type_label}
            </div>
            <img
              src={circlePostImageUrl(previewPost.id)}
              alt={previewPost.card_type_label}
              style={{ width: "100%", borderRadius: 8, background: "#f5f2ea" }}
            />
          </>
        )}
      </Modal>
    </Card>
  );
}
