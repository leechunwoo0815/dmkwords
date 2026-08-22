import { useEffect, useState } from "react";
import { App as AntdApp, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography } from "antd";

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
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>(undefined);
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

  const categories = [...new Set(configs.map((c) => c.category))];
  const filtered = categoryFilter ? configs.filter((c) => c.category === categoryFilter) : configs;

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
      message.success(`配置 ${editing.config_key} 已更新`);
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
      <Typography.Title level={4} style={{ fontFamily: "Georgia, 'Songti SC', serif" }}>
        系统配置
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        全部业务数值集中管理；修改即生效并自动写入审计日志（需填写原因）。
      </Typography.Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <Select
          placeholder="按分类筛选"
          allowClear
          style={{ width: 160 }}
          value={categoryFilter}
          onChange={setCategoryFilter}
          options={categories.map((c) => ({ value: c, label: c }))}
        />
      </Space>
      <Table<SystemConfig>
        rowKey="config_key"
        loading={loading}
        dataSource={filtered}
        size="middle"
        pagination={{ pageSize: 15, showSizeChanger: false }}
        columns={[
          { title: "配置键", dataIndex: "config_key", width: 260 },
          {
            title: "类型",
            dataIndex: "value_type",
            width: 80,
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
                <Tag color={v === "true" ? "green" : "default"}>{v === "true" ? "开启" : "关闭"}</Tag>
              ) : (
                <Typography.Text code>{v}</Typography.Text>
              ),
          },
          {
            title: "默认值",
            dataIndex: "default_value",
            width: 140,
            render: (v: string) => <Typography.Text type="secondary">{v}</Typography.Text>,
          },
          { title: "分类", dataIndex: "category", width: 90 },
          { title: "说明", dataIndex: "description", ellipsis: true },
          {
            title: "操作",
            key: "action",
            width: 90,
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
        title={`修改配置：${editing?.description ?? editing?.config_key ?? ""}`}
        open={editing !== null}
        onOk={saveEdit}
        onCancel={() => setEditing(null)}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="value" label="新值" rules={[{ required: true, message: "请输入新值" }]}>
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
