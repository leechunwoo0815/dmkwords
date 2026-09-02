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
| WM3 | 会员与订单（人工收款路径） | MANUAL_TEST | gate.sh full exit 0（提交 710d56d/543593a/448ede2）| 家长孩子/订单/收款/会员状态机/二孩折扣；等手动验收。**20260831 UX 基线修复 DONE**：W1-W12（家长搜索建档/待确认计数/脏数据保护/URL 镜像/排序/到期天数/演示异常态等），门禁 229 passed / 80.02%（gate-runs/2026-08-31/gate-wm3-ux.log）；两刀 commit 未 push |
| WM4 | 押金与赔偿 | MANUAL_TEST | gate.sh full exit 0（提交 74efe86）| 押金状态机/扣除/补缴/待结清；等手动验收 |
| WM5 | 借阅操作台 | MANUAL_TEST | gate.sh full exit 0（提交 7b5a8ee）| 借/还/续/逾期扣减/人工放行/未入会开关；等手动验收 |
| WM6 | 小程序与阅读链 + 预约 | MANUAL_TEST | gate.sh full exit 0（73 pytest + 架构关 + 覆盖率 60.73%）| 防刷按 R-151 墙上时钟重写；miniapp 9 页 + admin 预约管理/核销/阅读档案；等手动验收 |
| WM7 | 测验与成长 | MANUAL_TEST | gate.sh full exit 0（提交 4f7a985；81 pytest）| Quiz/词数/积分/等级/里程碑；等手动验收 |
| WM8 | 榜单报告护照 | MANUAL_TEST | gate.sh full exit 0（提交 077a2eb；90 pytest）| 五榜单/护照/周月报图片/生词本/收藏夹；等手动验收 |
| WM9 | 线下活动 | MANUAL_TEST | gate.sh full exit 0（提交 73e76d6；97 pytest）| 发布/报名/入场券/签到/退款矩阵/99 元链；等手动验收 |
| WM10 | 退款退会转让 | MANUAL_TEST | gate.sh full exit 0（提交 e6dadd3；104 pytest，覆盖率 69.6%）| 退款中心/退会/权益转让/评估报告；等手动验收 |
| P0-fix | docs/09 P0 四项（D1/C13/C15/C16 + C17 随修） | DONE | gate.sh full exit 0（119 pytest，覆盖率 75.19%）；测试 tests/unit/test_d1_member_gates.py 10 用例 | 会员过期即时判定（读时不等定时任务）+ 涟漪修复（音频仅在手/周期榜/转让/二孩折扣）；观察期→待评估馆员按钮+评估通过转正链；未入会放行限 1 本+72h 借期；AR 超范围软提示（ar_warning_range 配置）。**已提交**（commit `af2eac2`） |
| P0-fix2 | docs/10 P0 五项（R-313 守卫 / 退会 6 态 / 退款 7 态+refund_status / 会员费退款联动退会 / 转让 12 步）+ C20/WM10-07/WM3-03/WM4-01 | DONE | gate.sh full exit 0（2026-08-24，128 pytest / 覆盖率 76.50%）；test_wm10_state_machines 5/5、test_wm10_transfer_refund 7/7、test_r313_guards 4/4、test_d1_member_gates 10/10；架构关 PASS（transfer_service.py 拆分）；前端 RefundCenter.tsx 已适配（执行退款按钮+7/6 态中文标签，tsc 0 错） | MDL 挂起根因：①RefundService.review 未推进联动退会 applying→refunding（execute 失败分支失效）②_advance_withdrawal 补 pending_settle ③测试 _db() 改 with 防断言泄漏。**已提交**（commit `af2eac2` 代码 + `28f54eb` docs 回写） |
| WM11 | 通知任务看板 | GATE_PASS | 2026-08-29 最终全量门禁 PASS（216 passed / 79.51%，gate-runs/2026-08-29/gate-wm11-ux.log；同日演进 gate-wm11.log→fix.log→ux.log 三份全 PASS）；WM11 专项测试 25（tests/unit/test_wm11_*.py，含审查 P0 并发隔离 + P1 转让超时） | 通知中心（站内必达+微信尽力+发送记录）+ 12 项定时任务（D1 第 3 层过期落库/C13 待评估转换/预约释放/订单超时/转让超时/借阅逾期/活动提醒等）+ 任务看板/手动触发/运行日志 + 死信落库（D6）+ 数据看板补全（D5）+ Excel 导出（C18 审计/看板/通知）+ miniapp 消息页。**手动验收主体已通过**（管理端七步 + miniapp 消息中心 + 自动调度取证闭环；验收期修复 F1-F5/C41-C52 见 docs/17 §八）；遗留：miniapp 查词 UI 复测、看板七字段抽查。详册 docs/17 |
| WM12 | 微信支付与收尾 | PENDING | — | |
| P0-fix3 | 安全漏洞五项 + 验收必撞两卡（20260831 外部审查） | DONE | 2026-09-01 全量门禁 PASS（gate-runs/2026-09-01/gate-p0-fixes.log，275 passed / 81.18%，首轮 FAIL×3 整改后重跑）；并发/安全红测试 10（wm6_parent_guard 3 / wm6_login_guard 4 / wm2_media_auth 3）；专家裁定 ✅ 通过（浏览器目视补位 + 2 返工项闭环）| F1 reading 6 端点归属校验 / F2 验证码 fail-closed（LOGIN_DEV_CODE 配置化）/ F3 媒体 token type+gen（middleware validate_admin_payload 抽取）/ F4 押金 Decimal 防负数 + billing func.or_ 同族 / F5 登录页 placeholder；WM5-F1 续借错行 / WM13-F4 tab 计数同源+null 返工；commit dc58701/30ea058/08b8936/47ac550/d727381/e099b33/d271355/adeacc3/900dd48/cf80094/eb77e6e/64aab81；不 push 等验收 |
| WM3 插修 2 | 首轮目视反馈 8 卡（F1-F8） | DONE | 2026-09-02 C 级全量 gate PASS（gate-runs/2026-09-02/gate-wm3-fix2.log，310 passed / 81.52%，基线 305→310 只增不减；首轮 FAIL×2：mkdir 老坑+format 残留→收敛后 PASS）；红测试 5（F2 1+F7 4）；BDD 6 场景全绿（+environment 业务表清理补齐）；E-03 反模式全站对照（F5 同族 obsUpload 一并修）| F6 client.ts 基建双修（FormData Content-Type+422 detail 数组）；F1 同族第三次（家长 tab 计数，sweep 清单附简报）；F7 守卫细化（身份锁/学籍放，用户拍板）；不 push 等目视复测 |
| WM3 插修 1 | 验收反馈 13 卡（20260901 任务包） | DONE | 2026-09-01 C 级全量 gate PASS（gate-runs/2026-09-01/gate-wm3-fix1.log，305 passed / 81.41%，基线 287→305 只增不减；首轮 FAIL×3 如实整改：F841/架构关四违规→OrderService 拆分 876→469+B2 端点下沉+voucher_auth middleware）；红测试 17（B1 10+B2 6+顺带 2）；BDD 4 场景独立 feature（membership @draft 存量勿动）；A1 同族 sweep 全站零同款 | A1-A5/D1-D3/B4 小修 8 刀；B1 编辑删除守卫双保险（FEAT-074/R-315 解禁口径）；B2 凭证上传查看（FEAT-075，迁移 a3b5c7d9e1f3 串链修双 head，member.manage 独立权限）；顺带 1-3 全清；不 push 等验收；目视未做（如实声明）|
| P1 | 资金并发行锁统一包（20260831 审查） | DONE | 2026-09-01：10 卡 + 2 追加项全落地（F1 退款 execute/review 行锁/F2 confirm_payment+押金幂等/F3 押金 deduct/F5 还书+overdue_mark 守卫/F4 预约释放锁序/F8 锁序 Child 最先/F6 99 元锁内复查/F7 apply 查重加锁/F9 双 commit 合一+F8 拆分/F10 dev 8443 禁调度/get_current_admin 收敛）；全量门禁 287 passed / 81.33%（gate-runs/2026-09-01/gate-p1-locks.log）；并发测试 11（session_pair 基建）；专家裁定 ✅ 通过（记录项：简报 hash 全错已纠——以后 hash 一律 git log 实取）；两个深层发现入记忆库 E-20260901-01（populate_existing/autoflush）；god file 整改（reading 拆 vocabulary_service.py 201 行）顺带；**顺带待修挂账：checkout res 锁定读补 populate_existing（一行，随下一批）** | commit 2f30aeb/b4975b5/23898cc/d0cb4bd/fd396fb/99a22d9/f857e4e/f4ea8f3/ccc5b37/dd725b3/64aab81/28f8176；不 push 等验收 |
| WM13 | 运营审核工作台（管理端通知/待办聚合/双 tab 通知中心） | MANUAL_TEST | 任务包 docs/任务包-20260831-WM13运营审核工作台.md；五批次 DONE 2026-08-31：底座（admin_notifications+StatusResolver 实时算显示态+5 触发点）/收件箱双 tab/徽标+待办卡/转让超时预警任务/9 处 L2 审计回写+RefundCenter 定位高亮；专项测试 32 | 等手动验收（docs/04 WM13 验收表 10 步）；显示态实时算/审计态事件写（S1 结构性歼灭） |

## 待办（非模块主线）

| # | 任务 | 状态 | 备注 |
|---|---|---|---|
| T1 | OrbStack 安装 → MySQL 3306 → 真实库冒烟 | DONE | 2026-08-22 完成，旧服务已清 | |
| T2 | 按图施工手册（docs/07） | DONE | 2026-08-28 产出并入库（413 行；含第十二章 UX 增强基线，commit d757511） |
| T3 | 微信资质确认（小程序主体/类目/商户号/订阅模板） | PENDING | 甲方跟进，阻塞 WM12 |
| T4 | 需求签字状态跟踪 | PENDING | P3+ 业务域以签字为前提 |
| T5 | PRD 复审补充任务（P0×4/P1×6/P2×10/债×4） | IN_DEV | 详单 docs/09-补充任务清单.md；P0/P1 已全部修完（门禁证据见 gate-runs/ 最新日志）；D3 已修（CI 重写，首绿 run）；剩余 P2×8（C2/C3/C5/C6/C9/C10/C11/C18）与 WM11 统筹 |
| T6 | 目录清理与文档债（任务包 20260831） | DONE | 2026-08-31 完成：删无引用 png、paint-v3/v4 与两过程报告入 legacy-attic、docs/04 补 miniapp v5/v6 验收表（14 行）；纯文档 commit |
| T7 | 外部专家全量审查 + 专家资格交接 | DONE | 2026-08-31：外部专家全库审查产出 16 文件修复包（外部专家意见/20260831-全量审查修复包/，~100 卡）；前任专家抽验 13 项声明 13/13 实锤后完成资格交接（docs/专家交接书-20260831-资格交接.md）；P0 安全五项 + P1 资金行锁十项待投开发模型 |

## 状态机

PENDING → IN_DEV → GATE_PASS（自动门禁 exit 0，贴 gate-runs 证据）→ MANUAL_TEST（用户按手册测）→ ACCEPTED / REWORK → GATE_PASS（循环）
