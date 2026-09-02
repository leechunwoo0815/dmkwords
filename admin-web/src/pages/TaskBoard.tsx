import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, message, Popconfirm, Popover, Table, Tag } from "antd";
import { PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";

import PaintEmpty from "../components/PaintEmpty";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import {
  apiRunTask,
  apiTaskRuns,
  apiTaskSpecs,
  TaskRunItem,
  TaskSpecItem,
} from "../api/admin";

const GROUP_COLORS: Record<string, string> = {
  会员: "blue",
  借阅: "green",
  资金: "red",
  活动: "orange",
};

function statusTag(status: string): React.ReactNode {
  switch (status) {
    case "success":
      return <Tag color="green">成功</Tag>;
    case "failed":
      return <Tag color="red">失败</Tag>;
    case "skipped":
      return <Tag color="orange">跳过</Tag>;
    default:
      return <Tag color="default">运行中</Tag>;
  }
}

function lastRunCell(last: TaskSpecItem["last_run"] | undefined): React.ReactNode {
  if (!last) {
    return <span style={{ color: "rgba(0,0,0,0.45)" }}>从未运行</span>;
  }
  const node = (
    <span>
      {statusTag(last.status)} {last.started_at}
    </span>
  );
  if (last.status === "failed" && last.error) {
    return (
      <Popover
        content={<div style={{ maxWidth: 320, whiteSpace: "pre-wrap" }}>{last.error}</div>}
        trigger="hover"
      >
        <span style={{ cursor: "help" }}>{node}</span>
      </Popover>
    );
  }
  return node;
}

export default function TaskBoard() {
  const [specs, setSpecs] = useState<TaskSpecItem[]>([]);
  const [runs, setRuns] = useState<TaskRunItem[]>([]);
  const [running, setRunning] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([apiTaskSpecs(), apiTaskRuns(20)]);
      setSpecs(s.items);
      setRuns(r.items);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // F4/C40：运行记录任务名用注册表 display_name 显示中文；历史任务映射不到回退英文
  const nameMap = useMemo(
    () => Object.fromEntries(specs.map((s) => [s.name, s.display_name])),
    [specs]
  );

  const trigger = async (name: string, displayName: string) => {
    setRunning(name);
    try {
      const result = await apiRunTask(name);
      if (result.status === "success") {
        message.success(`${displayName} 执行成功（处理 ${result.processed ?? 0} 条）`);
      } else {
        message.error(`${displayName} 执行失败：${result.error ?? ""}`);
      }
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setRunning(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card
        title="定时任务"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        }
      >
        <Table<TaskSpecItem>
          rowKey="name"
          size="small"
          loading={loading}
          dataSource={specs}
          locale={{ emptyText: <PaintEmpty message="暂无任务" /> }}
          pagination={false}
          columns={[
            { title: "任务", dataIndex: "display_name", width: 160 },
            {
              title: "分组",
              dataIndex: "group",
              width: 90,
              render: (g: string) => <Tag color={GROUP_COLORS[g] ?? "default"}>{g}</Tag>,
            },
            {
              title: "执行周期",
              dataIndex: "interval_seconds",
              width: 110,
              render: (s: number) =>
                s >= 86400 ? "每天" : s >= 3600 ? `每 ${s / 3600} 小时` : `每 ${s / 60} 分钟`,
            },
            {
              title: "上次运行",
              dataIndex: "last_run",
              width: 200,
              render: (last: TaskSpecItem["last_run"] | undefined) => lastRunCell(last),
            },
            {
              title: "操作",
              width: 110,
              render: (_: unknown, r) => (
                <Popconfirm
                  title={`确认手动执行「${r.display_name}」？`}
                  onConfirm={() => void trigger(r.name, r.display_name)}
                >
                  <Button
                    size="small"
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    loading={running === r.name}
                  >
                    手动触发
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
         scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      </Card>
      <Card title="运行记录">
        <Table<TaskRunItem>
          rowKey={(r) => `${r.task_name}-${r.started_at}`}
          size="small"
          dataSource={runs}
          pagination={false}
          expandable={{
            expandedRowRender: (r) =>
              r.error ? (
                <div style={{ whiteSpace: "pre-wrap" }}>失败原因：{r.error}</div>
              ) : (
                <div style={{ color: "rgba(0,0,0,0.45)" }}>本次运行无异常</div>
              ),
          }}
          columns={[
            { title: "任务", dataIndex: "task_name", width: 180, render: (v: string) => nameMap[v] ?? v },
            { title: "结果", dataIndex: "status", width: 90, render: statusTag },
            { title: "处理条数", dataIndex: "processed", width: 100 },
            { title: "开始时间", dataIndex: "started_at", width: 180 },
            {
              title: "失败原因",
              dataIndex: "error",
              ellipsis: true,
              render: (v: string | null) =>
                v ?? <span style={{ color: "rgba(0,0,0,0.45)" }}>—</span>,
            },
          ]}
         scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      </Card>
    </div>
  );
}