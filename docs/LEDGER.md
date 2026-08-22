# LEDGER 任务台账（唯一进度事实源）

> 规则（宪法第六节）：每轮开工读本文件取第一个未闭环项；状态流转必须先落证据（gate-runs/ 输出）再改本表；禁止凭记忆报进度。

## 模块交付主线（详册：docs/04-模块交付顺序与手动验收手册.md）

| # | 任务 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| M0 | 项目骨架 + 门禁 + 宪法 | DONE | commit 820631f; gate.sh full exit 0 | 2026-08-22 |
| M0.5 | Feature 全量清单 + Gherkin 初稿 | DONE | commit 78388a1; 230 场景解析通过 | 2026-08-22 |
| WM1 | 平台基座（登录/RBAC/配置/审计 + admin-web 骨架 + dev.sh） | MANUAL_TEST | gate.sh full exit 0（27 pytest + 12 BDD 场景 + 架构关 + 覆盖率 26%）| 等用户按手册 §WM1 手动验收 |
| WM2 | 图书资产 | MANUAL_TEST | gate.sh full exit 0（详见 20260822 提交 aa6e164/35ab440/7a195b8）| 书目/副本/导入/封面音频/题目；等手动验收 |
| WM3 | 会员与订单（人工收款路径） | MANUAL_TEST | gate.sh full exit 0（提交 710d56d/543593a/448ede2）| 家长孩子/订单/收款/会员状态机/二孩折扣；等手动验收 |
| WM4 | 押金与赔偿 | MANUAL_TEST | gate.sh full exit 0（提交 74efe86）| 押金状态机/扣除/补缴/待结清；等手动验收 |
| WM5 | 借阅操作台 | MANUAL_TEST | gate.sh full exit 0（提交 7b5a8ee）| 借/还/续/逾期扣减/人工放行/未入会开关；等手动验收 |
| WM6 | 小程序与阅读链 + 预约 | MANUAL_TEST | gate.sh full exit 0（73 pytest + 架构关 + 覆盖率 60.73%）| 防刷按 R-151 墙上时钟重写；miniapp 9 页 + admin 预约管理/核销/阅读档案；等手动验收 |
| WM7 | 测验与成长 | PENDING | — | |
| WM8 | 榜单报告护照 | PENDING | — | |
| WM9 | 线下活动 | PENDING | — | |
| WM10 | 退款退会转让 | PENDING | — | |
| WM11 | 通知任务看板 | PENDING | — | |
| WM12 | 微信支付与收尾 | PENDING | — | |

## 待办（非模块主线）

| # | 任务 | 状态 | 备注 |
|---|---|---|---|
| T1 | OrbStack 安装 → MySQL 3306 → 真实库冒烟 | DONE | 2026-08-22 完成，旧服务已清 | |
| T2 | 按图施工手册（docs/07） | PENDING | WM1 Demo 走完后产出 |
| T3 | 微信资质确认（小程序主体/类目/商户号/订阅模板） | PENDING | 甲方跟进，阻塞 WM12 |
| T4 | 需求签字状态跟踪 | PENDING | P3+ 业务域以签字为前提 |

## 状态机

PENDING → IN_DEV → GATE_PASS（自动门禁 exit 0，贴 gate-runs 证据）→ MANUAL_TEST（用户按手册测）→ ACCEPTED / REWORK → GATE_PASS（循环）
