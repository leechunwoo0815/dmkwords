# LEDGER 任务台账（唯一进度事实源）


> **跨会话交接**：读 `docs/项目交接-20260824.md`（当前状态 / 待办 / 血泪教训 / 恢复口令）。
> `docs/项目交接-20260823.md` 已归档为历史参考。

> 规则（宪法第六节）：每轮开工读本文件取第一个未闭环项；状态流转必须先落证据（gate-runs/ 输出）再改本表；禁止凭记忆报进度。

## 模块交付主线（详册：docs/04-模块交付顺序与手动验收手册.md）

| # | 任务 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| M0 | 项目骨架 + 门禁 + 宪法 | DONE | commit 820631f; gate.sh full exit 0 | 2026-08-22 |
| M0.5 | Feature 全量清单 + Gherkin 初稿 | DONE | commit 78388a1; 230 场景解析通过 | 2026-08-22 |
| WM1 | 平台基座（登录/RBAC/配置/审计 + admin-web 骨架 + dev.sh） | ACCEPTED | 2026-08-24 用户手动验收通过（11 步全过，含 C21 仪表盘 + C12 员工菜单核对） | 登录/RBAC/配置/审计/仪表盘/员工管理 |
| admin-web-ui | admin-web 绘本风视觉系统改造 | ACCEPTED | tsc 0 errors；gate.sh 147 passed / 78.11%；专家复验通过；commit dee28e5 | 不含 miniapp；含弹窗点击修复、P0/P1/P2 清理 |
| F1-F4 | 图书管理运营增强（分页/批量/进度条/滚动条） | ACCEPTED | 用户在 WM2 验收过程中逐项确认；commits `e4da393`/`4a43752`/`0bfe411`/`7e4a38f`/`c369b4b` | 已随 WM2 验收闭环 |
| admin-web-fix | 侧边栏固定 + 媒体重传缓存失效修复 | DONE | gate full PASS（148 passed / 77.94%）；tsc 0 错；commit `5980516` | 侧边栏不再随内容滚动；封面/音频重传后 URL 带 v 版本参数强制刷新；用户已验收 |
| WM2 | 图书资产 | ACCEPTED | **2026-08-28 用户手动验收通过**；外部终审 R1-R9 + P2 十二项 + C1-C3 + D1-D3 全部修复；gate PASS 187 passed/78.72%（gate-runs/2026-08-28/ 下 gate-wm2-p1.log、gate-wm2-p2.log、gate-wm2-d1d3.log 三份日志）；tag `acceptance-baseline-wm3-wm10`（固定于 ed60377） | 书目/副本/导入/封面音频/题目 + 运营增强 + 上架强校验（新书下架入库 + 五项完整性拦截 + book_onboarding_check 开关）|
| WM3 | 会员与订单（人工收款路径） | MANUAL_TEST | gate.sh full exit 0（提交 710d56d/543593a/448ede2）| 家长孩子/订单/收款/会员状态机/二孩折扣；等手动验收 |
| WM4 | 押金与赔偿 | MANUAL_TEST | gate.sh full exit 0（提交 74efe86）| 押金状态机/扣除/补缴/待结清；等手动验收 |
| WM5 | 借阅操作台 | MANUAL_TEST | gate.sh full exit 0（提交 7b5a8ee）| 借/还/续/逾期扣减/人工放行/未入会开关；等手动验收 |
| WM6 | 小程序与阅读链 + 预约 | MANUAL_TEST | gate.sh full exit 0（73 pytest + 架构关 + 覆盖率 60.73%）| 防刷按 R-151 墙上时钟重写；miniapp 9 页 + admin 预约管理/核销/阅读档案；等手动验收 |
| WM7 | 测验与成长 | MANUAL_TEST | gate.sh full exit 0（提交 4f7a985；81 pytest）| Quiz/词数/积分/等级/里程碑；等手动验收 |
| WM8 | 榜单报告护照 | MANUAL_TEST | gate.sh full exit 0（提交 077a2eb；90 pytest）| 五榜单/护照/周月报图片/生词本/收藏夹；等手动验收 |
| WM9 | 线下活动 | MANUAL_TEST | gate.sh full exit 0（提交 73e76d6；97 pytest）| 发布/报名/入场券/签到/退款矩阵/99 元链；等手动验收 |
| WM10 | 退款退会转让 | MANUAL_TEST | gate.sh full exit 0（提交 e6dadd3；104 pytest，覆盖率 69.6%）| 退款中心/退会/权益转让/评估报告；等手动验收 |
| P0-fix | docs/09 P0 四项（D1/C13/C15/C16 + C17 随修） | DONE | gate.sh full exit 0（119 pytest，覆盖率 75.19%）；测试 tests/unit/test_d1_member_gates.py 10 用例 | 会员过期即时判定（读时不等定时任务）+ 涟漪修复（音频仅在手/周期榜/转让/二孩折扣）；观察期→待评估馆员按钮+评估通过转正链；未入会放行限 1 本+72h 借期；AR 超范围软提示（ar_warning_range 配置）。**已提交**（commit `af2eac2`） |
| P0-fix2 | docs/10 P0 五项（R-313 守卫 / 退会 6 态 / 退款 7 态+refund_status / 会员费退款联动退会 / 转让 12 步）+ C20/WM10-07/WM3-03/WM4-01 | DONE | gate.sh full exit 0（2026-08-24，128 pytest / 覆盖率 76.50%）；test_wm10_state_machines 5/5、test_wm10_transfer_refund 7/7、test_r313_guards 4/4、test_d1_member_gates 10/10；架构关 PASS（transfer_service.py 拆分）；前端 RefundCenter.tsx 已适配（执行退款按钮+7/6 态中文标签，tsc 0 错） | MDL 挂起根因：①RefundService.review 未推进联动退会 applying→refunding（execute 失败分支失效）②_advance_withdrawal 补 pending_settle ③测试 _db() 改 with 防断言泄漏。**已提交**（commit `af2eac2` 代码 + `28f54eb` docs 回写） |
| WM11 | 通知任务看板 | PENDING | — | 含 D1 第 3 层（过期落库定时任务）+ C13 自动转换 |
| WM12 | 微信支付与收尾 | PENDING | — | |

## 待办（非模块主线）

| # | 任务 | 状态 | 备注 |
|---|---|---|---|
| T1 | OrbStack 安装 → MySQL 3306 → 真实库冒烟 | DONE | 2026-08-22 完成，旧服务已清 | |
| T2 | 按图施工手册（docs/07） | DONE | 2026-08-28 产出并入库（413 行；含第十二章 UX 增强基线，commit d757511） |
| T3 | 微信资质确认（小程序主体/类目/商户号/订阅模板） | PENDING | 甲方跟进，阻塞 WM12 |
| T4 | 需求签字状态跟踪 | PENDING | P3+ 业务域以签字为前提 |
| T5 | PRD 复审补充任务（P0×4/P1×6/P2×10/债×4） | IN_DEV | 详单 docs/09-补充任务清单.md；P0/P1 已全部修完（门禁证据见 gate-runs/ 最新日志）；D3 已修（CI 重写，首绿 run）；剩余 P2×8（C2/C3/C5/C6/C9/C10/C11/C18）与 WM11 统筹 |

## 状态机

PENDING → IN_DEV → GATE_PASS（自动门禁 exit 0，贴 gate-runs 证据）→ MANUAL_TEST（用户按手册测）→ ACCEPTED / REWORK → GATE_PASS（循环）
