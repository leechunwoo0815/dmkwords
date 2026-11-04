// admin-web/src/components/PageTitle.tsx — 页面标题（第一百六十四批改版）
//
// 历史：第一版是「深底霓虹绿」样稿，第二版是暖色绘本版（纸感底 + 深墨粗描边 + 硬阴影 + 等宽字距），
// 两版都靠 `.page-title-chip` 的 CSS 变量拼装。企业级改版后统一为
// **20px/600 标题 + 主色短竖条**（`.page-title`），不再有底框/阴影/字距，`tone` 参数随之下线。
//
// ⚠️ **使用边界（2026-09-21 用户反馈"太大了，也很丑"后定的）**：
//   只用于**页面名**（"借阅操作台""会员管理"这类 2–6 字的固定标签）。
//   **动态内容不要用**——书名/人名/活动名长度不可控。那类页面用普通
//   `Typography.Title` + `margin: 0` 即可。
import { Typography } from "antd";

export default function PageTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="page-title">
      <Typography.Title level={4} style={{ margin: 0 }}>
        {children}
      </Typography.Title>
    </div>
  );
}
