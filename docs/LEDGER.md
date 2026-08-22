# LEDGER 任务台账（唯一进度事实源）

> 规则（宪法第六节）：每轮开工读本文件取第一个未闭环项；状态流转必须先落证据（gate-runs/ 输出）再改本表；禁止凭记忆报进度。

## 模块交付主线（详册：docs/04-模块交付顺序与手动验收手册.md）

| # | 任务 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| M0 | 项目骨架 + 门禁 + 宪法 | DONE | commit 820631f; gate.sh full exit 0 | 2026-08-22 |
| M0.5 | Feature 全量清单 + Gherkin 初稿 | DONE | commit 78388a1; 230 场景解析通过 | 2026-08-22 |
| WM1 | 平台基座（登录/RBAC/配置/审计 + admin-web 骨架 + dev.sh） | PENDING | — | 含完整流程 Demo |
| WM2 | 图书资产 | PENDING | — | |
| WM3 | 会员与订单（人工收款路径） | PENDING | — | |
| WM4 | 押金与赔偿 | PENDING | — | |
| WM5 | 借阅操作台 | PENDING | — | |
| WM6 | 小程序与阅读链 + 预约 | PENDING | — | |
| WM7 | 测验与成长 | PENDING | — | |
| WM8 | 榜单报告护照 | PENDING | — | |
| WM9 | 线下活动 | PENDING | — | |
| WM10 | 退款退会转让 | PENDING | — | |
| WM11 | 通知任务看板 | PENDING | — | |
| WM12 | 微信支付与收尾 | PENDING | — | |

## 待办（非模块主线）

| # | 任务 | 状态 | 备注 |
|---|---|---|---|
| T1 | OrbStack 安装（brew 进行中）→ 起 MySQL → 真实库冒烟 | IN_PROGRESS | 装完需用户点首次向导 |
| T2 | 按图施工手册（docs/07） | PENDING | WM1 Demo 走完后产出 |
| T3 | 微信资质确认（小程序主体/类目/商户号/订阅模板） | PENDING | 甲方跟进，阻塞 WM12 |
| T4 | 需求签字状态跟踪 | PENDING | P3+ 业务域以签字为前提 |

## 状态机

PENDING → IN_DEV → GATE_PASS（自动门禁 exit 0，贴 gate-runs 证据）→ MANUAL_TEST（用户按手册测）→ ACCEPTED / REWORK → GATE_PASS（循环）
