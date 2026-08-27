import { Button, Col, InputNumber, Pagination, Row, Space, Typography } from "antd";

const PRESETS = [15, 30, 50, 70, 100];

interface PaintPaginationProps {
  current: number;
  pageSize: number;
  total: number;
  onChange: (page: number, pageSize: number) => void;
}

export default function PaintPagination({ current, pageSize, total, onChange }: PaintPaginationProps) {
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
        <Pagination
          current={current}
          pageSize={pageSize}
          total={total}
          showQuickJumper
          showSizeChanger={false}
          showTotal={(t, range) => `${range[0]}-${range[1]} / 共 ${t} 条`}
          onChange={(p, s) => onChange(p, s)}
        />
      </Col>
    </Row>
  );
}
