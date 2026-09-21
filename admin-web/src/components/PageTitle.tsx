// admin-web/src/components/PageTitle.tsx — 页面大标题（样稿，2026-09-21）
//
// 用户要求：大标题「改个字体 + 加个底色框」，先改一页看效果。
// 第一版做了深底霓虹绿（tone="neon"），用户反馈「颜色风格好像不太搭」——
// 站点是暖色绘本风（米纸底 + 深墨粗描边 + 硬阴影），冷色调的霓虹框确实跳。
// 所以现在默认走 `tone="warm"`：**同样是"换字体 + 底色框"，但配色回到站点令牌**——
// 纸感底 + 深墨描边 + 硬阴影 + 主色橙左条 + 等宽字撑出"科技感"，与周围组件同族。
// 两版都留在 CSS 里，用户可对比后再定（改一个 prop 即可全站切换）。
//
// 留白口径与会员管理一致：Title `margin: 0`（AntD 默认 margin-top 1.2em 是"顶上一大片白"的真凶）。
//
// ⚠️ **使用边界（2026-09-21 用户反馈"太大了，也很丑"后定的）**：
//   只用于**页面名**（"借阅操作台""会员管理"这类 2–6 字的固定标签）。
//   **动态内容不要用**——书名/人名/活动名长度不可控，等宽字距会把它撑成大黑框
//   （图书详情页试过，用户当场否掉）。那类页面用普通 `Typography.Title` + `margin: 0` 即可。
import { Typography } from "antd";

export default function PageTitle({
  children,
  tone = "warm",
}: {
  children: React.ReactNode;
  tone?: "warm" | "neon";
}) {
  return (
    <div className={`page-title-chip${tone === "neon" ? " page-title-chip--neon" : ""}`}>
      <span className="page-title-bar" />
      <Typography.Title level={4} className="page-title-text" style={{ margin: 0 }}>
        {children}
      </Typography.Title>
      <span className="page-title-dot" />
    </div>
  );
}
