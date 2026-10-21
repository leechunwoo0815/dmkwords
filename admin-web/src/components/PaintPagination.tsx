import { Button, Col, InputNumber, Pagination, Row, Space, Typography } from "antd";
import { useRef } from "react";

const PRESETS = [15, 30, 50, 70, 100];

interface PaintPaginationProps {
  current: number;
  pageSize: number;
  total: number;
  onChange: (page: number, pageSize: number) => void;
  /**
   * P2-44：后端硬上限提示。传后端返回的 `X-List-Limit`（当前生效的上限）即可——
   * 组件在"已加载行数已达上限"时显示提示条：**当前列表可能不是全部**。
   * 不传则不显示（后端未提供元信息的列表保持原样）。
   */
  listLimit?: number | null;
  /**
   * 本页接口的每页上限（默认 100）。超上限的预设按钮**不显示**——避免"能选却必 422"（P2-56）。
   */
  maxPageSize?: number;
}

export default function PaintPagination({
  current, pageSize, total, onChange, listLimit = null, maxPageSize = 100,
}: PaintPaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const lastJumpRef = useRef<number | null>(null);
  // 已达上限 = 后端可能还有没返回的行（不猜总数，只如实说"可能还有更多"）
  const capped = listLimit !== null && total >= listLimit;

  return (
    <Row justify="space-between" align="middle" style={{ marginTop: 16 }}>
      <Col>
        <Space>
          <Typography.Text>每页显示</Typography.Text>
          <InputNumber
            min={1}
            max={maxPageSize}
            value={pageSize}
            onChange={(value) => onChange(1, value ?? pageSize)}
            style={{ width: 70 }}
          />
          <Typography.Text>条</Typography.Text>
          <Space size={4}>
            {PRESETS.filter((n) => n <= maxPageSize).map((n) => (
              <Button
                key={n}
                size="small"
                type={pageSize === n ? "primary" : "default"}
                onClick={() => onChange(1, n)}
              >
                {n}
              </Button>
            ))}
          </Space>
        </Space>
      </Col>
      <Col>
        <Space>
          {capped && (
            <Typography.Text type="warning" data-testid="list-capped-hint">
              已达后端上限 {listLimit} 条，可能还有更多未显示——请用筛选条件缩小范围
            </Typography.Text>
          )}
          <Pagination
            current={current}
            pageSize={pageSize}
            total={total}
            showQuickJumper={false}
            showSizeChanger={false}
            showTotal={(t, range) => `${range[0]}-${range[1]} / 共 ${t} 条`}
            onChange={(p, s) => onChange(p, s)}
          />
          <Space>
            <Typography.Text>跳至</Typography.Text>
            <InputNumber
              min={1}
              max={totalPages}
              onChange={(value) => {
                const n = Number(value);
                if (
                  Number.isInteger(n) &&
                  n >= 1 &&
                  n <= totalPages &&
                  n !== lastJumpRef.current
                ) {
                  lastJumpRef.current = n;
                  onChange(n, pageSize);
                }
              }}
              style={{ width: 70 }}
            />
            <Typography.Text>页</Typography.Text>
          </Space>
        </Space>
      </Col>
    </Row>
  );
}
