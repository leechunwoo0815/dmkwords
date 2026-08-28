import { Button, Col, InputNumber, Pagination, Row, Space, Typography } from "antd";
import { useRef } from "react";

const PRESETS = [15, 30, 50, 70, 100];

interface PaintPaginationProps {
  current: number;
  pageSize: number;
  total: number;
  onChange: (page: number, pageSize: number) => void;
}

export default function PaintPagination({ current, pageSize, total, onChange }: PaintPaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const lastJumpRef = useRef<number | null>(null);

  return (
    <Row justify="space-between" align="middle" style={{ marginTop: 16 }}>
      <Col>
        <Space>
          <Typography.Text>每页显示</Typography.Text>
          <InputNumber
            min={1}
            max={200}
            value={pageSize}
            onChange={(value) => onChange(1, value ?? pageSize)}
            style={{ width: 70 }}
          />
          <Typography.Text>条</Typography.Text>
          <Space size={4}>
            {PRESETS.map((n) => (
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
