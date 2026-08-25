import { useCallback, useEffect, useState } from "react";
import {
  App as AntdApp,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from "antd";
import { DeleteOutlined, InboxOutlined, PlusOutlined } from "@ant-design/icons";

import {
  apiBatchDeleteBooks,
  apiCreateBook,
  apiDeleteBook,
  apiDownloadImportTemplate,
  apiImportBooks,
  apiListBooks,
  apiToggleBookStatus,
  type Book,
} from "../api/catalog";
import { GRADE_OPTIONS } from "../constants/grade";

export default function BookManage() {
  const { message } = AntdApp.useApp();
  const [books, setBooks] = useState<Book[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [tab, setTab] = useState("all");
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [form] = Form.useForm();
  const [importResult, setImportResult] = useState<{
    total_rows: number;
    success_count: number;
    failed_count: number;
    errors: string[];
  } | null>(null);
  const [searchValue, setSearchValue] = useState("");
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);

  const doSearch = useCallback((v: string) => {
    setKeyword(v);
    setPage(1);
  }, []);

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      apiListBooks({
        page: targetPage,
        page_size: 15,
        keyword: keyword || undefined,
        ar_pending: tab === "ar",
        status: tab === "on" ? 1 : tab === "off" ? 0 : undefined,
        no_cover: tab === "no_cover",
        no_audio: tab === "no_audio",
        quiz_incomplete: tab === "quiz_incomplete",
      })
        .then((result) => {
          setBooks(result.items ?? []);
          setTotal(result.total);
        })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [keyword, tab, message]
  );

  useEffect(() => {
    load(page);
  }, [load, page]);

  const submitCreate = async () => {
    const values = await form.validateFields();
    try {
      await apiCreateBook({
        isbn: values.isbn || null,
        title: values.title,
        author: values.author ?? "",
        word_count: values.word_count,
        ar_level: values.ar_level || null,
        topic: values.topic ?? "",
        grade: values.grade ?? "",
        description: values.description || null,
        copy_count: values.copy_count ?? 1,
      });
      message.success(`《${values.title}》入库成功`);
      setCreateOpen(false);
      form.resetFields();
      load(1);
      setPage(1);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "入库失败");
    }
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return;
    try {
      const result = await apiBatchDeleteBooks(selectedRowKeys);
      message.success(`批量删除完成：成功 ${result.success}，失败 ${result.failed}`);
      setSelectedRowKeys([]);
      load(page);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "批量删除失败");
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography.Title level={4} style={{ fontFamily: "'ZCOOL KuaiLe', 'Nunito', 'PingFang SC', sans-serif", marginBottom: 0 }}>
          图书管理
        </Typography.Title>
        <Space>
          <Button onClick={() => { apiDownloadImportTemplate().catch((e) => message.error((e as Error).message)); }}>下载导入模板</Button>
          <Button onClick={() => setImportOpen(true)}>Excel 批量导入</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新书入库
          </Button>
        </Space>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
        书目与实体副本管理；封面/音频/测验题目在书目详情中维护。
      </Typography.Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="书名 / 作者 / ISBN / 编号"
          allowClear
          style={{ width: 260 }}
          value={searchValue}
          onChange={(e) => {
            const v = e.target.value;
            setSearchValue(v);
            if (v === "") {
              doSearch("");
            }
          }}
          onSearch={doSearch}
          onClear={() => doSearch("")}
        />
        {selectedRowKeys.length > 0 && (
          <Popconfirm
            title={`确认删除选中的 ${selectedRowKeys.length} 条书目？`}
            description="删除后不可恢复，已借出副本会阻止删除。"
            onConfirm={handleBatchDelete}
          >
            <Button danger icon={<DeleteOutlined />}>批量删除 ({selectedRowKeys.length})</Button>
          </Popconfirm>
        )}
      </Space>
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k);
          setPage(1);
        }}
        items={[
          { key: "all", label: "全部" },
          { key: "on", label: "上架中" },
          { key: "off", label: "已下架" },
          { key: "ar", label: "AR 待配置" },
          { key: "no_cover", label: "未传封面" },
          { key: "no_audio", label: "未传音频" },
          { key: "quiz_incomplete", label: "测验未满 5 道" },
        ]}
      />
      <Table<Book>
        rowKey="id"
        loading={loading}
        dataSource={books}
        size="middle"
        pagination={{ current: page, pageSize: 15, total, showSizeChanger: false, onChange: setPage }}
        scroll={{ x: "max-content" }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as number[]),
          preserveSelectedRowKeys: false,
        }}
        columns={[
          { title: "书名", dataIndex: "title", width: 220, render: (t: string, r) => (
            <a onClick={() => window.open(`/books/${r.id}`, "_self")}>{t}</a>
          ) },
          { title: "作者", dataIndex: "author", width: 100, ellipsis: true },
          { title: "ISBN / 编号", key: "code", width: 130, render: (_, r) => (
            <Typography.Text code style={{ fontSize: 12 }}>{r.isbn ?? r.internal_code}</Typography.Text>
          ) },
          { title: "AR", dataIndex: "ar_level", width: 70, render: (v) => v ?? <Tag color="orange">待配置</Tag> },
          { title: "词数", dataIndex: "word_count", width: 80, render: (v: number) => v.toLocaleString() },
          { title: "副本", dataIndex: "copy_count", width: 60 },
          {
            title: "封面", dataIndex: "cover_path", width: 60,
            render: (v) => (v ? <Tag color="green">已传</Tag> : <Tag>未传</Tag>),
          },
          {
            title: "音频", dataIndex: "audio_path", width: 60,
            render: (v) => (v ? <Tag color="green">已传</Tag> : <Tag>未传</Tag>),
          },
          { title: "测验", dataIndex: "question_count", width: 60 },
          {
            title: "状态", dataIndex: "status", width: 70,
            render: (s: number) => (s === 1 ? <Tag color="green">上架</Tag> : <Tag>下架</Tag>),
          },
          {
            title: "操作", key: "op", width: 160,
            render: (_, r) => (
              <Space size="small">
                <Button type="link" size="small" onClick={() => window.open(`/books/${r.id}`, "_self")}>编辑</Button>
                <Popconfirm
                  title={r.status === 1 ? "下架后小程序不可见、不可借阅预约，已借出的仍需归还" : "确认恢复上架？"}
                  onConfirm={async () => {
                    await apiToggleBookStatus(r.id);
                    message.success(r.status === 1 ? "已下架" : "已上架");
                    load(page);
                  }}
                >
                  <Button type="link" size="small">{r.status === 1 ? "下架" : "上架"}</Button>
                </Popconfirm>
                <Popconfirm
                  title="确认删除该书目？删除后不可恢复"
                  onConfirm={async () => {
                    await apiDeleteBook(r.id);
                    message.success("已删除");
                    load(page);
                  }}
                >
                  <Button type="link" size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新书入库"
        open={createOpen}
        onOk={submitCreate}
        onCancel={() => setCreateOpen(false)}
        okText="入库"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="isbn" label="ISBN（没有可留空，系统自动编号）">
            <Input placeholder="如 9780545582889" />
          </Form.Item>
          <Form.Item name="title" label="书名" rules={[{ required: true, message: "请输入书名" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="author" label="作者">
            <Input />
          </Form.Item>
          <Space size="large">
            <Form.Item name="word_count" label="总词数" rules={[{ required: true, message: "请输入总词数" }]}>
              <InputNumber min={0} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="ar_level" label="AR 值（可后补）">
              <Input placeholder="如 2.5" style={{ width: 140 }} />
            </Form.Item>
          </Space>
          <Space size="large">
            <Form.Item name="topic" label="主题">
              <Input style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="grade" label="适读阶段">
              <Select options={GRADE_OPTIONS} allowClear placeholder="请选择适读阶段" style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="copy_count" label="入库副本数" initialValue={1}>
              <InputNumber min={1} max={99} style={{ width: 90 }} />
            </Form.Item>
          </Space>
          <Form.Item name="description" label="简介">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Excel 批量导入"
        open={importOpen}
        footer={null}
        onCancel={() => {
          setImportOpen(false);
          setImportResult(null);
        }}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary">
          列顺序：ISBN、书名、作者、AR 值、总词数、主题、适读阶段、副本数量。
          同 ISBN 再次导入 = 增加副本；错误行不影响其他行。
        </Typography.Paragraph>
        <Upload.Dragger
          accept=".xlsx,.xls"
          maxCount={1}
          showUploadList={false}
          customRequest={async ({ file, onSuccess, onError }) => {
            try {
              const result = await apiImportBooks(file as File);
              setImportResult(result);
              load(1);
              onSuccess?.(result);
            } catch (e) {
              message.error(e instanceof Error ? e.message : "导入失败");
              onError?.(e as Error);
            }
          }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽 Excel 文件到此处</p>
        </Upload.Dragger>
        {importResult && (
          <div style={{ marginTop: 16 }}>
            <Typography.Text>
              共 {importResult.total_rows} 行：成功 {importResult.success_count}，失败 {importResult.failed_count}
            </Typography.Text>
            {importResult.errors.length > 0 && (
              <div style={{ marginTop: 8, maxHeight: 200, overflow: "auto", background: "#FFFDF7", border: "2px solid #3B2F2F", padding: 12, borderRadius: 12 }}>
                {importResult.errors.map((e, i) => (
                  <div key={i} style={{ color: "#b54028", fontSize: 13 }}>{e}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
