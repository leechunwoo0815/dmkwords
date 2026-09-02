import PaintEmpty from "../components/PaintEmpty";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
// 员工管理（WM1 §11.1 超管职责：创建/禁用/改角色/重置密码，仅超管可见）
import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography,
} from "antd";

import {
  apiCreateStaff, apiListStaff, apiResetStaffPassword, apiSetStaffStatus, apiUpdateStaff,
  type AdminUser,
} from "../api/admin";

const ROLE_LABEL: Record<string, string> = { superadmin: "超级管理员", staff: "运营专员" };

interface CreateForm {
  username: string;
  password: string;
  display_name: string;
  role: string;
}

export default function Staff() {
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<AdminUser[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateForm>();
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [editForm] = Form.useForm<{ display_name: string; role: string }>();
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [newPwd, setNewPwd] = useState("");

  const load = useCallback(() => {
    apiListStaff().then(setRows).catch((e: Error) => message.error(e.message));
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const doCreate = async () => {
    const v = await form.validateFields();
    try {
      await apiCreateStaff(v);
      message.success("员工已创建");
      setCreateOpen(false);
      form.resetFields();
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doEdit = async () => {
    if (!editing) return;
    const v = await editForm.validateFields();
    try {
      await apiUpdateStaff(editing.id, v);
      message.success("已更新");
      setEditing(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doReset = async () => {
    if (!resetTarget) return;
    if (newPwd.length < 8) {
      message.warning("新密码至少 8 位");
      return;
    }
    try {
      await apiResetStaffPassword(resetTarget.id, newPwd);
      message.success("密码已重置");
      setResetTarget(null);
      setNewPwd("");
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography.Title level={4} style={{ marginBottom: 0 }}>
          员工管理
        </Typography.Title>
        <Space>
          <Button type="primary" onClick={() => setCreateOpen(true)}>新建员工</Button>
        </Space>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        员工=运营专员（借还/图书/活动/会员办理）；角色变更与停用仅超管可操作，全部操作留审计。
      </Typography.Paragraph>
      <Table<AdminUser> locale={{ emptyText: <PaintEmpty character="default" /> }}
        rowKey="id" dataSource={rows} size="middle" pagination={false}
        columns={[
          { title: "登录名", dataIndex: "username", width: 160, render: (v) => <Typography.Text code>{v}</Typography.Text> },
          { title: "显示名", dataIndex: "display_name" },
          { title: "角色", dataIndex: "role", width: 140, render: (r) => <Tag color={r === "superadmin" ? "gold" : "blue"}>{ROLE_LABEL[r] ?? r}</Tag> },
          {
            title: "状态", dataIndex: "status", width: 100,
            render: (s: number) => (s === 1 ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>),
          },
          {
            title: "操作", key: "op", width: 320, render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => { setEditing(r); editForm.setFieldsValue({ display_name: r.display_name, role: r.role }); }}>编辑</Button>
                <Button size="small" onClick={() => setResetTarget(r)}>重置密码</Button>
                {r.status === 1 ? (
                  <Popconfirm title={`禁用员工「${r.username}」？`} onConfirm={async () => {
                    try { await apiSetStaffStatus(r.id, 0); message.success("已禁用"); load(); } catch (e) { message.error((e as Error).message); }
                  }}>
                    <Button size="small" danger>禁用</Button>
                  </Popconfirm>
                ) : (
                  <Button size="small" onClick={async () => {
                    try { await apiSetStaffStatus(r.id, 1); message.success("已启用"); load(); } catch (e) { message.error((e as Error).message); }
                  }}>启用</Button>
                )}
              </Space>
            ),
          },
        ]}
       scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />

      <Modal title="新建员工" open={createOpen} okText="创建" cancelText="取消"
        onOk={doCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ role: "staff" }}>
          <Form.Item name="username" label="登录名" rules={[{ required: true, message: "必填" }, { pattern: /^[A-Za-z0-9_.-]+$/, message: "仅字母数字_.-", min: 2 }]}>
            <Input placeholder="staff02" />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, message: "必填" }, { min: 8, message: "至少 8 位" }]}>
            <Input.Password placeholder="至少 8 位" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名" rules={[{ required: true, message: "必填" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[{ value: "staff", label: "运营专员" }, { value: "superadmin", label: "超级管理员" }]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`编辑员工：${editing?.username ?? ""}`} open={!!editing} okText="保存" cancelText="取消"
        onOk={doEdit} onCancel={() => setEditing(null)} destroyOnClose>
        <Form form={editForm} layout="vertical">
          <Form.Item name="display_name" label="显示名" rules={[{ required: true, message: "必填" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[{ value: "staff", label: "运营专员" }, { value: "superadmin", label: "超级管理员" }]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`重置密码：${resetTarget?.username ?? ""}`} open={!!resetTarget} okText="确认重置" cancelText="取消"
        onOk={doReset} onCancel={() => setResetTarget(null)} destroyOnClose>
        <Input.Password value={newPwd} onChange={(e) => setNewPwd(e.target.value)} placeholder="新密码（至少 8 位）" />
      </Modal>
    </div>
  );
}
