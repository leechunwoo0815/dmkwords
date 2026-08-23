# LEDGER 任务台账（唯一进度事实源）


> **跨会话交接**：读 `docs/项目交接-20260823.md`（当前状态 / 待办 / 血泪教训 / 恢复口令）。

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
| WM7 | 测验与成长 | MANUAL_TEST | gate.sh full exit 0（提交 4f7a985；81 pytest）| Quiz/词数/积分/等级/里程碑；等手动验收 |
| WM8 | 榜单报告护照 | MANUAL_TEST | gate.sh full exit 0（提交 077a2eb；90 pytest）| 五榜单/护照/周月报图片/生词本/收藏夹；等手动验收 |
| WM9 | 线下活动 | MANUAL_TEST | gate.sh full exit 0（提交 73e76d6；97 pytest）| 发布/报名/入场券/签到/退款矩阵/99 元链；等手动验收 |
| WM10 | 退款退会转让 | MANUAL_TEST | gate.sh full exit 0（提交 e6dadd3；104 pytest，覆盖率 69.6%）| 退款中心/退会/权益转让/评估报告；等手动验收 |
| WM11 | 通知任务看板 | PENDING | — | |
| WM12 | 微信支付与收尾 | PENDING | — | |

## 待办（非模块主线）

| # | 任务 | 状态 | 备注 |
|---|---|---|---|
| T1 | OrbStack 安装 → MySQL 3306 → 真实库冒烟 | DONE | 2026-08-22 完成，旧服务已清 | |
| T2 | 按图施工手册（docs/07） | PENDING | WM1 Demo 走完后产出 |
| T3 | 微信资质确认（小程序主体/类目/商户号/订阅模板） | PENDING | 甲方跟进，阻塞 WM12 |
| T4 | 需求签字状态跟踪 | PENDING | P3+ 业务域以签字为前提 |
| T5 | PRD 复审补充任务（P0×4/P1×6/P2×10/债×4） | PENDING | 详单 docs/09-补充任务清单.md；P0 四项在用户验收前修 |

## 状态机

PENDING → IN_DEV → GATE_PASS（自动门禁 exit 0，贴 gate-runs 证据）→ MANUAL_TEST（用户按手册测）→ ACCEPTED / REWORK → GATE_PASS（循环）
