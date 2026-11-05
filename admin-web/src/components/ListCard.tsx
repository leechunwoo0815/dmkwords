import { Space } from "antd";

/**
 * 列表卡（飞书后台结构）：工具条（标题 + 总数 + 批量操作）→ 筛选区 → 内容（表格 + 分页）。
 *
 * 背景：改造前筛选框、标签页、表格直接铺在白纸上，没有任何区块边界——这是"毛坯感"的主因。
 * 统一成一张卡之后，列表页共用同一套骨架：卡头有标题和总数，筛选在浅灰带里，分页在卡底。
 */
export default function ListCard({
  title,
  count,
  extra,
  filter,
  tabs,
  children,
}: {
  /** 卡头标题，如「图书列表」 */
  title: React.ReactNode;
  /** 右侧计数文案，如「共 35 条」；不传则只显示标题 */
  count?: React.ReactNode;
  /** 卡头右侧操作（批量上架/下架/删除等，选中行时才由调用方渲染） */
  extra?: React.ReactNode;
  /** 筛选区（输入框/下拉/查询按钮），渲染在浅灰带上 */
  filter?: React.ReactNode;
  /** 分类标签页（如系统配置的分类、列表的状态页签），渲染在卡头下方 */
  tabs?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="list-card">
      <div className="list-card-head">
        <div className="list-card-title">
          {title}
          {count != null && <span className="list-card-count">{count}</span>}
        </div>
        {extra != null && <Space size="small">{extra}</Space>}
      </div>
      {filter != null && <div className="list-card-filter">{filter}</div>}
      {tabs != null && <div className="list-card-tabs">{tabs}</div>}
      {children}
    </div>
  );
}
