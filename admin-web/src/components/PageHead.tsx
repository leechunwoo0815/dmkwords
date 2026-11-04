import { Space } from "antd";
import { useLocation } from "react-router-dom";

import PageTitle from "./PageTitle";
import TechIcon from "./TechIcon";
import type { IconName } from "./TechIcon";

/** 路由 → 图标：页面标题左侧的自绘图标（新增页面只在这里补一行） */
const ICON_BY_PATH: Record<string, IconName> = {
  "/": "dashboard",
  "/books": "book",
  "/members": "member",
  "/deposits": "wallet",
  "/circulation": "borrow",
  "/reservations": "reserve",
  "/growth": "trophy",
  "/activities": "calendar",
  "/refund-center": "refund",
  "/circle": "circle",
  "/notifications": "bell",
  "/tasks": "task",
  "/staff": "staff",
  "/configs": "config",
  "/audit-logs": "audit",
};

/**
 * 页头（飞书后台结构）：标题 + 说明 + 右侧主操作。
 *
 * 为什么单独抽出来：以前每个页面自己拼 `display:flex; justify-content:space-between`，
 * 每个页面的间距/对齐都不一样，整站看起来"一页一个样"。统一成组件后，
 * 所有列表页的头部是同一个高度、同一条基线、同一套间距。
 */
export default function PageHead({
  title,
  desc,
  extra,
}: {
  title: React.ReactNode;
  desc?: React.ReactNode;
  extra?: React.ReactNode;
}) {
  const location = useLocation();
  const section = location.pathname === "/" ? "/" : `/${location.pathname.split("/")[1] ?? ""}`;
  const icon = ICON_BY_PATH[section];

  return (
    <div className="page-head">
      <div className="page-head-main">
        <div className="page-head-title-row">
          {icon && (
            <span className="page-head-icon" aria-hidden>
              <TechIcon name={icon} size={20} />
            </span>
          )}
          <PageTitle>{title}</PageTitle>
        </div>
        {desc != null && <div className="page-head-desc">{desc}</div>}
      </div>
      {extra != null && <Space>{extra}</Space>}
    </div>
  );
}
