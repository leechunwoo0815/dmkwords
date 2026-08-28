import PaintEmpty from "../components/PaintEmpty";
import { PaintHScrollbar } from "../components/PaintHScrollbar";
import PaintPagination from "../components/PaintPagination";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, InboxOutlined, PlusOutlined } from "@ant-design/icons";

import {
  apiBatchDeleteBooks,
  apiBatchToggleBookStatus,
  apiCreateBook,
  apiDeleteBook,
  apiDownloadImportTemplate,
  apiImportBooks,
  apiListBooks,
  apiToggleBookStatus,
  type Book,
} from "../api/catalog";
import { GRADE_OPTIONS } from "../constants/grade";
import { AR_LEVEL_RULE } from "../constants/book";
import { usePaintPagination } from "../hooks/usePaintPagination";

export default function BookManage() {
  const { message } = AntdApp.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  // P2-6：挂载时从 URL 恢复筛选/页码/关键词（URL 为写后镜像，replace 不堆栈）
  const urlTab = searchParams.get("tab") ?? "all";
  const urlPage = Math.max(1, Number(searchParams.get("page")) || 1);
  const urlKeyword = searchParams.get("keyword") ?? "";
  const [books, setBooks] = useState<Book[]>([]);
  const [total, setTotal] = useState(0);
  const { page, pageSize, setPage, onChange: onPageChange } = usePaintPagination(15, urlPage);
  const [keyword, setKeyword] = useState(urlKeyword);
  const [tab, setTab] = useState(urlTab);
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
  const [searchValue, setSearchValue] = useState(urlKeyword);
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([]);
  const [importing, setImporting] = useState(false);
  const [batchErrors, setBatchErrors] = useState<string[] | null>(null);
  // C3：Tab 计数（与列表筛选同口径，后端 counts 返回）
  const [tabCounts, setTabCounts] = useState<Record<string, number>>({});
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const [tableContent, setTableContent] = useState<HTMLElement | null>(null);

  // P2-6：tab/page/keyword 变化时写回 URL（replace 不堆历史；初始空参数时替换为无参）
  useEffect(() => {
    const params: Record<string, string> = {};
    if (tab !== "all") params.tab = tab;
    if (page !== 1) params.page = String(page);
    if (keyword) params.keyword = keyword;
    setSearchParams(params, { replace: true });
  }, [tab, page, keyword, setSearchParams]);

  const doSearch = useCallback((v: string) => {
    setKeyword(v);
    setPage(1);
  }, []);

  // P2-7：受控排序状态（null = 未排序，回默认 id desc）
  const [sortField, setSortField] = useState<"id" | "word_count" | "copy_count" | null>(null);
  const [sortOrder, setSortOrder] = useState<"ascend" | "descend" | null>(null);

  const load = useCallback(
    (targetPage: number) => {
      setLoading(true);
      apiListBooks({
        page: targetPage,
        page_size: pageSize,
        keyword: keyword || undefined,
        ar_pending: tab === "ar",
        status: tab === "on" ? 1 : tab === "off" ? 0 : undefined,
        no_cover: tab === "no_cover",
        no_audio: tab === "no_audio",
        quiz_incomplete: tab === "quiz_incomplete",
        sort: sortField ?? undefined,
        order: sortField && sortOrder ? (sortOrder === "ascend" ? "asc" : "desc") : undefined,
      })
        .then((result) => {
          setBooks(result.items ?? []);
          setTotal(result.total);
          setTabCounts(result.counts ?? {});
        })
        .catch((e: Error) => message.error(e.message))
        .finally(() => setLoading(false));
    },
    [keyword, tab, pageSize, sortField, sortOrder, message]
  );

  useEffect(() => {
    load(page);
  }, [load, page]);

  useEffect(() => {
    const find = () => {
      const el = tableWrapRef.current?.querySelector('.ant-table-content') as HTMLElement | null;
      if (el && el !== tableContent) setTableContent(el);
    };
    find();
    const id = requestAnimationFrame(find);
    const timer = setTimeout(find, 500);
    return () => {
      cancelAnimationFrame(id);
      clearTimeout(timer);
    };
  }, [books, loading, tableContent]);

  const submitCreate = async () => {
    const values = await form.validateFields();
    try {
      await apiCreateBook({
        isbn: (values.isbn || "").replace(/[\s\-]/g, "") || null, // P2-8：提交前清洗
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
      setBatchErrors(result.failed > 0 ? result.errors : null);
      message.success(`批量删除完成：成功 ${result.success}，失败 ${result.failed}`);
      setSelectedRowKeys([]);
      load(page);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "批量删除失败");
    }
  };

  const handleBatchToggle = async (status: 0 | 1) => {
    if (selectedRowKeys.length === 0) return;
    const action = status === 1 ? "上架" : "下架";
    try {
      const result = await apiBatchToggleBookStatus(selectedRowKeys, status);
      setBatchErrors(result.failed > 0 ? result.errors : null);
      message.success(`批量${action}完成：成功 ${result.success}，失败 ${result.failed}`);
      setSelectedRowKeys([]);
      load(page);
    } catch (e) {
      message.error(e instanceof Error ? e.message : `批量${action}失败`);
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography.Title level={4} style={{ fontFamily: "var(--font-display)", marginBottom: 0 }}>
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
          <>
            <Button icon={<ArrowUpOutlined />} onClick={() => handleBatchToggle(1)}>
              批量上架 ({selectedRowKeys.length})
            </Button>
            <Button icon={<ArrowDownOutlined />} onClick={() => handleBatchToggle(0)}>
              批量下架 ({selectedRowKeys.length})
            </Button>
            <Popconfirm
              title={`确认删除选中的 ${selectedRowKeys.length} 条书目？`}
              description="删除后不可恢复，已借出副本会阻止删除。"
              onConfirm={handleBatchDelete}
            >
              <Button danger icon={<DeleteOutlined />}>批量删除 ({selectedRowKeys.length})</Button>
            </Popconfirm>
          </>
        )}
      </Space>
      {batchErrors && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text type="danger">批量操作部分失败明细：</Typography.Text>
          <div style={{ marginTop: 4, maxHeight: 200, overflow: "auto", background: "#FFFDF7", border: "2px solid #3B2F2F", padding: 12, borderRadius: 12 }}>
            {batchErrors.map((e, i) => (
              <div key={i} style={{ color: "var(--paint-danger)", fontSize: 13 }}>{e}</div>
            ))}
          </div>
        </div>
      )}
      <Tabs
        activeKey={tab}
        onChange={(k) => {
          setTab(k);
          setPage(1);
        }}
        items={[
          { key: "all", label: `全部（${tabCounts.all ?? 0}）` },
          { key: "on", label: `上架中（${tabCounts.on ?? 0}）` },
          { key: "off", label: `已下架（${tabCounts.off ?? 0}）` },
          { key: "ar", label: `AR 待配置（${tabCounts.ar ?? 0}）` },
          { key: "no_cover", label: `未传封面（${tabCounts.no_cover ?? 0}）` },
          { key: "no_audio", label: `未传音频（${tabCounts.no_audio ?? 0}）` },
          { key: "quiz_incomplete", label: `测验未满 5 道（${tabCounts.quiz_incomplete ?? 0}）` },
        ]}
      />
      <div ref={tableWrapRef} style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <Table<Book> locale={{ emptyText: <PaintEmpty character="bookworm" /> }}
          rowKey="id"
          loading={loading}
          dataSource={books}
          size="middle"
          pagination={false}
          scroll={{ x: "max-content" }}
          onChange={(_pagination, _filters, sorter) => {
            const s = Array.isArray(sorter) ? sorter[0] : sorter;
            if (s && s.order && (s.field === "word_count" || s.field === "copy_count")) {
              setSortField(s.field as "word_count" | "copy_count");
              setSortOrder(s.order);
            } else {
              setSortField(null);
              setSortOrder(null);
            }
            setPage(1);
          }}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as number[]),
            preserveSelectedRowKeys: true,
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
          {
            title: "词数", dataIndex: "word_count", width: 90,
            sorter: true,
            sortOrder: sortField === "word_count" ? (sortOrder ?? null) : null,
            render: (v: number) => v.toLocaleString(),
          },
          {
            title: "副本", dataIndex: "copy_count", width: 70,
            sorter: true,
            sortOrder: sortField === "copy_count" ? (sortOrder ?? null) : null,
          },
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
                    try {
                      await apiToggleBookStatus(r.id);
                      message.success(r.status === 1 ? "已下架" : "已上架");
                      load(page);
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "操作失败");
                    }
                  }}
                >
                  <Button type="link" size="small">{r.status === 1 ? "下架" : "上架"}</Button>
                </Popconfirm>
                <Popconfirm
                  title="确认删除该书目？删除后不可恢复"
                  onConfirm={async () => {
                    try {
                      await apiDeleteBook(r.id);
                      message.success("已删除");
                      load(page);
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "删除失败");
                    }
                  }}
                >
                  <Button type="link" size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
        <PaintHScrollbar target={tableContent} />
      </div>
      <PaintPagination current={page} pageSize={pageSize} total={total} onChange={onPageChange} />

      <Modal
        title="新书入库"
        open={createOpen}
        onOk={submitCreate}
        onCancel={() => {
          if (form.isFieldsTouched()) {
            Modal.confirm({
              title: "有未保存的修改",
              content: "确定放弃当前编辑内容？",
              okText: "放弃修改",
              okButtonProps: { danger: true },
              cancelText: "继续编辑",
              onOk: () => {
                setCreateOpen(false);
                form.resetFields();
              },
            });
          } else {
            setCreateOpen(false);
          }
        }}
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
              <InputNumber min={1} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="ar_level" label="AR 值（可后补）" rules={[AR_LEVEL_RULE]}>
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
          disabled={importing}
          customRequest={async ({ file, onSuccess, onError }) => {
            setImporting(true);
            try {
              const result = await apiImportBooks(file as File);
              setImportResult(result);
              if (page === 1) load(1);
              else setPage(1);
              onSuccess?.(result);
            } catch (e) {
              message.error(e instanceof Error ? e.message : "导入失败");
              onError?.(e as Error);
            } finally {
              setImporting(false);
            }
          }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">{importing ? "导入中，请稍候…" : "点击或拖拽 Excel 文件到此处"}</p>
        </Upload.Dragger>
        {importResult && (
          <div style={{ marginTop: 16 }}>
            <Typography.Text>
              共 {importResult.total_rows} 行：成功 {importResult.success_count}，失败 {importResult.failed_count}
            </Typography.Text>
            {importResult.errors.length > 0 && (
              <div style={{ marginTop: 8, maxHeight: 200, overflow: "auto", background: "#FFFDF7", border: "2px solid #3B2F2F", padding: 12, borderRadius: 12 }}>
                {importResult.errors.map((e, i) => (
                  <div key={i} style={{ color: "var(--paint-danger)", fontSize: 13 }}>{e}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
