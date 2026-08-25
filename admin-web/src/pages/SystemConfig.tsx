import PaintEmpty from "../components/PaintEmpty";
import { useEffect, useMemo, useState } from "react";
import {
  App as AntdApp,
  Button,
  Form,
  Input,
  Modal,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";

import { apiListConfigs, apiUpdateConfig, type SystemConfig } from "../api/admin";
import { hasPermission, useAuth } from "../auth";

const TYPE_LABEL: Record<string, string> = {
  int: "整数",
  float: "数值",
  bool: "开关",
  string: "文本",
};

const TYPE_COLOR: Record<string, string> = {
  int: "blue",
  float: "blue",
  bool: "orange",
  string: "default",
};

export default function SystemConfigPage() {
  const { permissions } = useAuth();
  const { message } = AntdApp.useApp();
  const [configs, setConfigs] = useState<SystemConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState<string>("全部");
  const [editing, setEditing] = useState<SystemConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<{ value: string; reason: string }>();

  const canEdit = hasPermission(permissions, "config.update");

  const load = () => {
    setLoading(true);
    apiListConfigs()
      .then(setConfigs)
      .catch((e: Error) => message.error(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const categories = useMemo(
    () => ["全部", ...new Set(configs.map((c) => c.category))],
    [configs]
  );

  const filtered =
    category === "全部" ? configs : configs.filter((c) => c.category === category);

  const openEdit = (config: SystemConfig) => {
    setEditing(config);
    form.setFieldsValue({ value: config.config_value, reason: "" });
  };

  const saveEdit = async () => {
    if (!editing) return;
    const values = await form.validateFields();
    setSaving(true);
    try {
      await apiUpdateConfig(editing.config_key, values);
      message.success(`「${editing.display_name || editing.description}」已更新`);
      setEditing(null);
      load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Typography.Title level={4} style={{ fontFamily: "var(--font-display)" }}>
        系统配置
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        全部业务数值集中管理；修改立即生效并自动记录审计日志。
      </Typography.Paragraph>
      <Tabs
        activeKey={category}
        onChange={setCategory}
        items={categories.map((c) => ({ key: c, label: c }))}
      />
      <Table<SystemConfig> locale={{ emptyText: <PaintEmpty character="default" /> }}
        rowKey="config_key"
        loading={loading}
        dataSource={filtered}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: false, hideOnSinglePage: true }}
        columns={[
          {
            title: "配置项",
            key: "name",
            width: 240,
            render: (_, r) => (
              <div>
                <div>{r.display_name || r.description || r.config_key}</div>
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 12 }}
                  code
                >
                  {r.config_key}
                </Typography.Text>
              </div>
            ),
          },
          {
            title: "类型",
            dataIndex: "value_type",
            width: 70,
            render: (t: string) => (
              <Tag color={TYPE_COLOR[t] ?? "default"}>{TYPE_LABEL[t] ?? t}</Tag>
            ),
          },
          {
            title: "当前值",
            dataIndex: "config_value",
            width: 140,
            render: (v: string, record) =>
              record.value_type === "bool" ? (
                <Tag color={v === "true" ? "green" : "default"}>
                  {v === "true" ? "已开启" : "已关闭"}
                </Tag>
              ) : (
                <Typography.Text strong>{v}</Typography.Text>
              ),
          },
          {
            title: "默认值",
            dataIndex: "default_value",
            width: 120,
            render: (v: string, record) =>
              record.value_type === "bool" ? (
                <Typography.Text type="secondary">
                  {v === "true" ? "开启" : "关闭"}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">{v}</Typography.Text>
              ),
          },
          { title: "说明", dataIndex: "description", ellipsis: true },
          {
            title: "操作",
            key: "action",
            width: 80,
            render: (_, record) =>
              canEdit ? (
                <Button type="link" size="small" onClick={() => openEdit(record)}>
                  修改
                </Button>
              ) : null,
          },
        ]}
      />
      <Modal
        title={`修改：${editing?.display_name || editing?.description || ""}`}
        open={editing !== null}
        onOk={saveEdit}
        onCancel={() => setEditing(null)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        {editing && (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            当前值：
            <Typography.Text strong>
              {editing.value_type === "bool"
                ? editing.config_value === "true"
                  ? "已开启"
                  : "已关闭"
                : editing.config_value}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 12 }}>
              （系统标识 {editing.config_key}）
            </Typography.Text>
          </Typography.Paragraph>
        )}
        <Form form={form} layout="vertical">
          <Form.Item
            name="value"
            label="新值"
            rules={[{ required: true, message: "请输入新值" }]}
            extra={
              editing?.value_type === "bool"
                ? "填写 true（开启）或 false（关闭）"
                : editing?.value_type === "int"
                  ? "请输入整数"
                  : undefined
            }
          >
            <Input placeholder={editing?.value_type === "bool" ? "true / false" : "新值"} />
          </Form.Item>
          <Form.Item
            name="reason"
            label="变更原因（必填，写入审计日志）"
            rules={[{ required: true, message: "请填写变更原因" }]}
          >
            <Input.TextArea rows={2} placeholder="例如：开业促销临时调整" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
