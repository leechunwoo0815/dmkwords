import PaintEmpty from "../components/PaintEmpty";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  App as AntdApp,
  Button,
  Card,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import { ArrowLeftOutlined, PlusOutlined } from "@ant-design/icons";

import {
  apiAddCopies,
  apiCreateQuestion,
  apiDeleteQuestion,
  apiGetBook,
  apiListCopies,
  apiListQuestions,
  apiMediaUrl,
  apiToggleQuestion,
  apiUpdateBook,
  apiUpdateCopyStatus,
  apiUpdateQuestion,
  apiUploadAudio,
  apiUploadCover,
  type Book,
  type BookCopy,
  type QuizQuestion,
} from "../api/catalog";
import { GRADE_OPTIONS } from "../constants/grade";
import { AR_LEVEL_RULE } from "../constants/book";
import { PaintHScrollbar } from "../components/PaintHScrollbar";

const COPY_STATUS_LABEL: Record<string, string> = {
  available: "在馆", reserved: "预约锁定", borrowed: "已借出",
  maintenance: "维护中", lost: "遗失",
};
const COPY_STATUS_COLOR: Record<string, string> = {
  available: "green", reserved: "blue", borrowed: "orange",
  maintenance: "default", lost: "red",
};

// R3：副本状态下拉按当前状态动态渲染（后端转移矩阵的运维安全子集；
// available→reserved/borrowed 不开放手工转移——预约/借阅必须走各自业务流程）
const COPY_STATUS_TRANSITIONS: Record<string, { value: string; label: string }[]> = {
  available: [
    { value: "maintenance", label: "转维护" },
    { value: "lost", label: "标记遗失" },
  ],
  reserved: [
    { value: "available", label: "释放锁定" },
    { value: "maintenance", label: "转维护" },
  ],
  borrowed: [],
  maintenance: [
    { value: "available", label: "恢复在馆" },
    { value: "lost", label: "标记遗失" },
  ],
  lost: [
    { value: "available", label: "找回恢复" },
    { value: "maintenance", label: "转维护" },
  ],
};

export default function BookDetail() {
  const { id } = useParams<{ id: string }>();
  const bookId = Number(id);
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [book, setBook] = useState<Book | null>(null);
  const [copies, setCopies] = useState<BookCopy[]>([]);
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [coverProgress, setCoverProgress] = useState<number | null>(null);
  const [audioProgress, setAudioProgress] = useState<number | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [questionOpen, setQuestionOpen] = useState(false);
  const [qEditing, setQEditing] = useState<QuizQuestion | null>(null);
  const [qOptions, setQOptions] = useState<string[]>(["", "", "", ""]);
  const [qAnswerIdx, setQAnswerIdx] = useState(0);
  const [form] = Form.useForm();
  const [qForm] = Form.useForm();
  // P2-1：留痕原因/增加副本改正经 Modal（替代 window.prompt）
  const [addCopiesOpen, setAddCopiesOpen] = useState(false);
  const [addCopiesCount, setAddCopiesCount] = useState<number>(1);
  const [pendingCopy, setPendingCopy] = useState<{ copyId: number; newStatus: string; label: string } | null>(null);
  const [copyReason, setCopyReason] = useState("");
  // P2-11：题目 Modal 打开时快照（受控 state 的脏判定用对比实现，判断题 ["对","错"] 恒非空不可用 touched/非空启发式）
  const qSnapshotRef = useRef("");
  const takeQSnapshot = (opts: string[], idx: number) => {
    qSnapshotRef.current = JSON.stringify({ v: qForm.getFieldsValue(), o: opts, a: idx });
  };
  const isQDirty = () =>
    JSON.stringify({ v: qForm.getFieldsValue(), o: qOptions, a: qAnswerIdx }) !== qSnapshotRef.current;
  const confirmDiscardIfDirty = (dirty: boolean, onClose: () => void) => {
    if (!dirty) {
      onClose();
      return;
    }
    Modal.confirm({
      title: "有未保存的修改",
      content: "确定放弃当前编辑内容？",
      okText: "放弃修改",
      okButtonProps: { danger: true },
      cancelText: "继续编辑",
      onOk: onClose,
    });
  };

  const load = () => {
    apiGetBook(bookId).then(setBook).catch((e: Error) => message.error(e.message));
    apiListCopies(bookId)
      .then(setCopies)
      .catch(() => message.error("副本列表加载失败，请刷新重试"));
    apiListQuestions(bookId)
      .then(setQuestions)
      .catch(() => message.error("题目列表加载失败，请刷新重试"));
  };

  useEffect(load, [bookId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!book) return null;
  const openEdit = () => {
    form.setFieldsValue({
      isbn: book.isbn ?? "",
      title: book.title, author: book.author, word_count: book.word_count,
      ar_level: book.ar_level, topic: book.topic, grade: book.grade,
      description: book.description ?? "",
    });
    setEditOpen(true);
  };

  const openQuestionEditor = (q: QuizQuestion) => {
    qForm.setFieldsValue({
      question_type: q.question_type,
      question_text: q.question_text,
      bool_answer: q.answer,
    });
    setQEditing(q);
    const opts = q.question_type === "boolean" ? ["对", "错"] : [...q.options, "", "", "", ""].slice(0, Math.max(4, q.options.length));
    const idx = q.options.indexOf(q.answer);
    setQOptions(opts);
    setQAnswerIdx(idx >= 0 ? idx : 0);
    takeQSnapshot(opts, idx >= 0 ? idx : 0);
    setQuestionOpen(true);
  };

  const onQuestionSubmit = async () => {
    const values = await qForm.validateFields();
    try {
      if (values.question_type === "boolean") {
        const body = {
          question_type: values.question_type,
          question_text: values.question_text,
          options: ["对", "错"],
          answer: values.bool_answer,
        };
        if (qEditing) await apiUpdateQuestion(qEditing.id, body);
        else await apiCreateQuestion(bookId, body);
      } else {
        const trimmed = qOptions.map((o) => o.trim());
        if (trimmed.some((o) => !o)) {
          message.warning("所有选项都需要填写");
          return;
        }
        if (trimmed.length < 2) {
          message.warning("单选题至少 2 个选项");
          return;
        }
        const body = {
          question_type: String(values.question_type),
          question_text: String(values.question_text),
          options: trimmed,
          answer: trimmed[qAnswerIdx] ?? trimmed[0] ?? "",
        };
        if (qEditing) await apiUpdateQuestion(qEditing.id, body);
        else await apiCreateQuestion(bookId, body);
      }
      message.success(qEditing ? "题目已更新" : "题目已添加");
      setQuestionOpen(false);
      load();
    } catch (e) {
      message.error((e as Error).message || "保存失败，请检查填写内容");
    }
  };

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/books")}>返回</Button>
        <Typography.Title level={4} style={{ margin: 0, fontFamily: "var(--font-display)" }}>
          {book.title}
        </Typography.Title>
        {book.status === 1 ? <Tag color="green">上架</Tag> : <Tag>下架</Tag>}
        {/* D1：下架态展示缺失清单，指引馆员完善 */}
        {book.status === 0 && (book.missing?.length ?? 0) > 0 && (
          <Tag color="orange">待完善：{(book.missing ?? []).join("、")}</Tag>
        )}
        <Button onClick={openEdit}>编辑信息</Button>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space size="large" wrap>
          <span>作者：<Typography.Text strong>{book.author || "—"}</Typography.Text></span>
          <span>ISBN/编号：<Typography.Text code>{book.isbn ?? book.internal_code}</Typography.Text></span>
          <span>总词数：<Typography.Text strong>{book.word_count.toLocaleString()}</Typography.Text></span>
          <span>AR 值：{book.ar_level ?? <Tag color="orange">待配置</Tag>}</span>
          <span>副本：{copies.length} 本</span>
        </Space>
      </Card>

      {/* 封面与音频 */}
      <Card title="封面与音频" size="small" style={{ marginBottom: 16 }}>
        <Space size="large" wrap>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>封面</Typography.Text>
            <div style={{ margin: "4px 0 8px" }}>
              {book.cover_path ? (
                <Image
                  src={apiMediaUrl(bookId, "cover", book.cover_path ?? undefined)}
                  alt="封面"
                  width={72}
                  height={100}
                  style={{ objectFit: "cover", borderRadius: 6, border: "1px solid var(--paint-border)" }}
                  preview={{ mask: "预览" }}
                />
              ) : (
                <div style={{ width: 72, height: 100, borderRadius: 6, border: "1px dashed var(--paint-border)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--paint-ink-light)", fontSize: 12 }}>未上传</div>
              )}
            </div>
            <Upload
              accept=".jpg,.jpeg,.png,.webp" maxCount={1} showUploadList={false}
              disabled={coverProgress !== null}
              customRequest={async ({ file, onSuccess, onError }) => {
                setCoverProgress(0);
                try {
                  await apiUploadCover(bookId, file as File, (p) => setCoverProgress(p));
                  // D1 收尾：load() 全量刷新（missing 等派生字段以后端最新计算为准）
                  load();
                  message.success("封面上传成功（已统一转 JPG）");
                  setCoverProgress(null);
                  onSuccess?.(null);
                } catch (e) {
                  message.error(e instanceof Error ? e.message : "上传失败");
                  setCoverProgress(null);
                  onError?.(e as Error);
                }
              }}
            >
              <Button size="small" loading={coverProgress !== null}>上传封面</Button>
            </Upload>
            {coverProgress !== null && (
              <Progress percent={coverProgress} size="small" status="active" style={{ width: 160, marginTop: 6 }} />
            )}
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>配套音频（仅 MP3）</Typography.Text>
            <div style={{ margin: "4px 0 8px" }}>
              {book.audio_path ? (
                <Space>
                  <Tag color="green">已上传</Tag>
                  {book.audio_duration_seconds ? <Typography.Text type="secondary">约 {Math.ceil(book.audio_duration_seconds / 60)} 分钟</Typography.Text> : null}
                  <audio
                    controls preload="metadata" style={{ width: 320, height: 36 }}
                    src={apiMediaUrl(bookId, "audio", book.audio_path ?? undefined)}
                  />
                </Space>
              ) : (
                <Tag>未上传</Tag>
              )}
            </div>
            <Upload
              accept=".mp3" maxCount={1} showUploadList={false}
              disabled={audioProgress !== null}
              customRequest={async ({ file, onSuccess, onError }) => {
                setAudioProgress(0);
                try {
                  await apiUploadAudio(bookId, file as File, (p) => setAudioProgress(p));
                  // D1 收尾：load() 全量刷新（missing/时长等派生字段以后端最新计算为准）
                  load();
                  message.success("音频上传成功");
                  setAudioProgress(null);
                  onSuccess?.(null);
                } catch (e) {
                  message.error(e instanceof Error ? e.message : "上传失败");
                  setAudioProgress(null);
                  onError?.(e as Error);
                }
              }}
            >
              <Button size="small" loading={audioProgress !== null}>上传音频</Button>
            </Upload>
            {audioProgress !== null && (
              <Progress percent={audioProgress} size="small" status="active" style={{ width: 320, marginTop: 6 }} />
            )}
          </div>
        </Space>
      </Card>

      {/* 副本管理 */}
      <Card
        title="实体副本"
        size="small" style={{ marginBottom: 16 }}
        extra={
          <Button size="small" icon={<PlusOutlined />} onClick={() => setAddCopiesOpen(true)}>增加副本</Button>
        }
      >
        <Table<BookCopy> locale={{ emptyText: <PaintEmpty character="bookworm" /> }}
          rowKey="id" dataSource={copies} size="small" pagination={false}
          columns={[
            { title: "副本码", dataIndex: "copy_code", width: 160, render: (v) => <Typography.Text code>{v}</Typography.Text> },
            { title: "状态", dataIndex: "status", width: 110, render: (s: string) => <Tag color={COPY_STATUS_COLOR[s]}>{COPY_STATUS_LABEL[s] ?? s}</Tag> },
          {
            title: "操作", key: "op", width: 160,
            render: (_, r) => (
              <Select
                size="small" style={{ width: 120 }} value={r.status}
                disabled={COPY_STATUS_TRANSITIONS[r.status]?.length === 0}
                onChange={(v) => {
                  const label = COPY_STATUS_TRANSITIONS[r.status]?.find((o) => o.value === v)?.label ?? v;
                  setPendingCopy({ copyId: r.id, newStatus: v, label });
                  setCopyReason("");
                }}
                options={[
                  // 复核修复：当前状态必须作为禁用首选项，否则 Select 找不到匹配项会显示英文原始值
                  { value: r.status, label: COPY_STATUS_LABEL[r.status] ?? r.status, disabled: true },
                  ...(COPY_STATUS_TRANSITIONS[r.status] ?? []),
                ]}
              />
            ),
          },
          ]}
         scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      </Card>

      {/* 测验题目 */}
      <Card
        title={`测验题目（取前 ${Math.min(5, questions.filter((q) => q.is_active === 1).length)} 道作为正式题；多余备用）`}
        size="small"
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => {
          qForm.resetFields();
          setQEditing(null);
          setQOptions(["", "", "", ""]);
          setQAnswerIdx(0);
          takeQSnapshot(["", "", "", ""], 0);
          setQuestionOpen(true);
        }}>添加题目</Button>}
      >
        <Table<QuizQuestion> locale={{ emptyText: <PaintEmpty character="bookworm" /> }}
          rowKey="id" dataSource={questions} size="small" pagination={false}
          columns={[
            { title: "#", dataIndex: "sort_order", width: 50 },
            { title: "题型", dataIndex: "question_type", width: 80, render: (t) => (t === "boolean" ? "判断" : "单选") },
            { title: "题干", dataIndex: "question_text", ellipsis: true },
            { title: "选项", dataIndex: "options", width: 260, ellipsis: true, render: (o: string[]) => o.join(" / ") },
            { title: "答案", dataIndex: "answer", width: 110 },
            { title: "状态", dataIndex: "is_active", width: 80, render: (v: number) => (v === 1 ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>) },
            {
              title: "操作", key: "op", width: 170,
              render: (_, r) => (
                <Space>
                  <Button type="link" size="small" onClick={() => openQuestionEditor(r)}>编辑</Button>
                  <Button type="link" size="small" onClick={async () => {
                    try {
                      await apiToggleQuestion(r.id);
                      load();
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "操作失败");
                    }
                  }}>
                    {r.is_active === 1 ? "停用" : "启用"}
                  </Button>
                  <Popconfirm title="确认删除该题目？" onConfirm={async () => {
                    try {
                      await apiDeleteQuestion(r.id);
                      message.success("已删除");
                      load();
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : "删除失败");
                    }
                  }}>
                    <Button type="link" size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
         scroll={{ x: "max-content" }}/>
          <PaintHScrollbar auto />
      </Card>

      <Modal
        title="编辑书目信息" open={editOpen}
        onOk={async () => {
          const values = await form.validateFields();
          try {
            await apiUpdateBook(bookId, {
              ...values,
              isbn: (values.isbn || "").replace(/[\s\-]/g, "") || null, // P2-8：提交前清洗
            });
            message.success("已保存");
            setEditOpen(false);
            load();
          } catch (e) {
            message.error(e instanceof Error ? e.message : "保存失败");
          }
        }}
        onCancel={() => confirmDiscardIfDirty(form.isFieldsTouched(), () => setEditOpen(false))}
        okText="保存" cancelText="取消" destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="isbn" label="ISBN（可留空或修改；清空后自动转为内部编号）">
            <Input placeholder="如 9780545582889" />
          </Form.Item>
          <Form.Item name="title" label="书名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="author" label="作者"><Input /></Form.Item>
          <Space size="large">
            <Form.Item
              name="word_count"
              label="总词数"
              rules={[
                { required: true },
                // D2：显式拦截静默 clamp
                { validator: (_r, v) => (v === null || v === undefined || v >= 1 ? Promise.resolve() : Promise.reject(new Error("总词数至少为 1"))) },
              ]}
            >
              <InputNumber min={1} style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="ar_level" label="AR 值" rules={[AR_LEVEL_RULE]}><Input style={{ width: 140 }} /></Form.Item>
          </Space>
          <Space size="large">
            <Form.Item name="topic" label="主题"><Input style={{ width: 140 }} /></Form.Item>
            <Form.Item name="grade" label="适读阶段"><Select options={GRADE_OPTIONS} allowClear placeholder="请选择" style={{ width: 160 }} /></Form.Item>
          </Space>
          <Form.Item name="description" label="简介"><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>

      <Modal
        title="增加副本" open={addCopiesOpen}
        onOk={async () => {
          try {
            await apiAddCopies(bookId, addCopiesCount);
            message.success(`已增加 ${addCopiesCount} 本副本`);
            setAddCopiesOpen(false);
            setAddCopiesCount(1);
            load();
          } catch (e) {
            message.error(e instanceof Error ? e.message : "增加副本失败");
          }
        }}
        onCancel={() => { setAddCopiesOpen(false); setAddCopiesCount(1); }}
        okText="确认增加" cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            副本码按书目编号自动生成，增加后可在下方副本表查看。
          </Typography.Text>
          <div>
            <Typography.Text>数量（1-99）：</Typography.Text>
            <InputNumber min={1} max={99} value={addCopiesCount} onChange={(v) => setAddCopiesCount(v ?? 1)} style={{ width: 100, marginLeft: 8 }} />
          </div>
        </Space>
      </Modal>

      <Modal
        title={pendingCopy ? `副本状态变更：${pendingCopy.label}` : "操作原因（留痕）"}
        open={pendingCopy !== null}
        onOk={async () => {
          if (!pendingCopy) return;
          try {
            await apiUpdateCopyStatus(pendingCopy.copyId, { status: pendingCopy.newStatus, reason: copyReason.trim() });
            message.success("副本状态已更新");
            setPendingCopy(null);
            load();
          } catch (e) {
            message.error(e instanceof Error ? e.message : "操作失败");
          }
        }}
        onCancel={() => setPendingCopy(null)}
        okText="确认变更" cancelText="取消"
        okButtonProps={{ disabled: !copyReason.trim() }}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">操作原因将写入审计日志（必填，≤200 字）</Typography.Text>
          <Input.TextArea
            rows={3}
            maxLength={200}
            showCount
            value={copyReason}
            placeholder="如：预约超时释放 / 修复破损 / 找回遗失副本"
            onChange={(e) => setCopyReason(e.target.value)}
          />
        </Space>
      </Modal>

      <Modal
        title={qEditing ? "编辑题目" : "添加测验题目"} open={questionOpen}
        onOk={onQuestionSubmit}
        onCancel={() => confirmDiscardIfDirty(isQDirty(), () => setQuestionOpen(false))}
        okText={qEditing ? "保存" : "添加"} cancelText="取消" destroyOnClose
      >
        <Form form={qForm} layout="vertical" initialValues={{ question_type: "single" }}>
          <Form.Item name="question_type" label="题型">
            <Select options={[{ value: "single", label: "单选题" }, { value: "boolean", label: "判断题" }]} />
          </Form.Item>
          <Form.Item name="question_text" label="题干" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item noStyle shouldUpdate={(a, b) => a.question_type !== b.question_type}>
            {({ getFieldValue }) =>
              getFieldValue("question_type") === "single" ? (
                <>
                  <Form.Item label="选项（可增删）" required>
                    <div>
                      {qOptions.map((opt, i) => (
                        <Space key={i} style={{ display: "flex", marginBottom: 6 }}>
                          <Input
                            value={opt}
                            style={{ width: 240 }}
                            placeholder={`选项 ${String.fromCharCode(65 + i)}`}
                            onChange={(e) => setQOptions((old) => old.map((o, j) => (j === i ? e.target.value : o)))}
                          />
                          <Button size="small" type="text" danger disabled={qOptions.length <= 2}
                            onClick={() => setQOptions((old) => old.filter((_, j) => j !== i))}>
                            删除
                          </Button>
                        </Space>
                      ))}
                      <Button size="small" type="dashed" disabled={qOptions.length >= 6}
                        onClick={() => setQOptions((old) => [...old, ""])}>
                        添加选项
                      </Button>
                    </div>
                  </Form.Item>
                  <Form.Item label="正确答案" required>
                    <Radio.Group value={qAnswerIdx} onChange={(e) => setQAnswerIdx(e.target.value)}>
                      {qOptions.map((_, i) => (
                        <Radio key={i} value={i} disabled={!qOptions[i]?.trim()}>
                          {String.fromCharCode(65 + i)}
                        </Radio>
                      ))}
                    </Radio.Group>
                  </Form.Item>
                </>
              ) : (
                <Form.Item name="bool_answer" label="正确答案" rules={[{ required: true }]}>
                  <Radio.Group>
                    <Radio value="对">对</Radio>
                    <Radio value="错">错</Radio>
                  </Radio.Group>
                </Form.Item>
              )
            }
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
