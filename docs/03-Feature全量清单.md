# 03 Feature 全量清单（DmkWords v1.0）

> 唯一事实源：业务需求文档 V1.1 + 需求决策链 V1.0.1（R-xxx 锚点）。
> 旧项目 feature 仅作"边界维度提醒"，其内容禁止进入本项目（防火墙纪律见 features/legacy/README.md）。
> 优先级：P0 = 上线必须 / P1 = 重要可稍后 / P2 = 本期可缓。
> 状态：清单待写（PLANNED）→ Gherkin 初稿（DRAFTED）→ 开发（IN_DEV）→ 验收（ACCEPTED）。

## 总览

- 12 个 Feature 包（F0-F11，与顶层规划 P1-P12 对应）× 70 项 Feature；
- 每项 Feature 的完整验收标准 = 本清单"验收标准"列 + 对应 Gherkin 文件场景集（双层结构，场景文件不重复抄元数据）；
- 状态汇总：PLANNED 0 / DRAFTED 70 / IN_DEV 0 / ACCEPTED 0。

## F0 账号与基建（identity + admin，对应 P1）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准（摘要） | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-001 | 家长账号注册与微信登录 | 一切操作的入口 | P0 | — | 微信 code 换 token；手机号绑定；一个手机号一个账号 | account.feature | DRAFTED |
| FEAT-002 | 孩子档案管理 | 多孩家庭基础 | P0 | 001 | 添加/编辑孩子；每孩子独立数据；切换当前孩子；孩子无独立账号〔V1.1 §2.2〕 | account.feature | DRAFTED |
| FEAT-003 | RBAC 角色权限 | 权限红线 | P0 | 001 | 仅超管+运营专员两角色；权限码声明式校验；三方一致（前端路由/后端接口/菜单）〔红线：数据权限〕 | rbac.feature | DRAFTED |
| FEAT-004 | SystemConfig 配置中心 | 数值全配置化铁律 | P0 | 003 | 全部业务数值进配置；读取带 TTL 缓存；变更审计留痕；类型校验 | config.feature | DRAFTED |
| FEAT-005 | 操作日志审计 | 放行留痕依据 | P0 | 003 | 谁何时对谁做了什么+原因；资金/状态变更必记；可查询导出 | audit.feature | DRAFTED |
| FEAT-006 | 同意记录（隐私协议） | 未成年人合规 | P0 | 001 | 协议版本化；同意/撤回留痕；撤回触发数据清理流程预留〔模式手册 P17〕 | account.feature | DRAFTED |

## F1 图书资产（catalog，对应 P2）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-007 | 书目管理 | 馆藏基础 | P0 | 004 | CRUD；ISBN 唯一（无 ISBN 走内部编号）；AR 值可后补（"待配置"筛选）；词数必填〔V1.1 §4.2〕 | catalog.feature | DRAFTED |
| FEAT-008 | 实体副本管理 | 多副本预留 | P0 | 007 | 书目 1:N 副本；默认 1 本；同 ISBN 再导入=加副本不重建书目〔V1.1 §4.1〕 | catalog.feature | DRAFTED |
| FEAT-009 | Excel 批量导入 | 几千本书开馆 | P0 | 007 | 模板下载；逐行校验报错（行号+原因）；部分成功部分失败可查；ISBN 重复并入 | catalog.feature | DRAFTED |
| FEAT-010 | 封面上传 | 展示 | P0 | 007 | JPG/JPEG/PNG/WebP 统一转 JPG 存储；路径 cover/{isbn前4}/{isbn13}.jpg；无 ISBN 走 local/；重复上传覆盖〔R-316〕 | catalog.feature | DRAFTED |
| FEAT-011 | 音频上传管理 | 听读核心 | P0 | 007 | 仅 MP3；路径 book_audio/{isbn13}/audio.mp3；可后补；无音频书提示不可计词数〔R-316, V1.1 §4.2〕 | catalog.feature | DRAFTED |
| FEAT-012 | 测验题目管理 | Quiz 前置 | P0 | 007 | 单选/判断题；一书多题备用（首期前 5 道）；改题/停用不影响已提交成绩快照〔V1.1 §6.4〕 | quiz_admin.feature | DRAFTED |
| FEAT-013 | 图书上下架 | 内容管控 | P0 | 007 | 下架即隐藏/禁借/停音频/禁新测验；已借仍可还；已得词数不回收〔V1.1 §4.4〕 | catalog.feature | DRAFTED |
| FEAT-014 | 副本状态流转 | 遗失/维护闭环 | P0 | 008 | 在馆/借出/维护/遗失/找回五态；转移矩阵+非法流转拦截；借出/归还/登记遗失驱动 | catalog.feature | DRAFTED |

## F2 订单支付退款（billing，对应 P3）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-015 | 订单模型与状态机 | 资金基石 | P0 | 004 | 6 类订单（first_activity/observation/formal/activity/deposit/deposit_supplement）× 5 态（pending_payment/pending_manual_confirm/paid/cancelled/refunded；域H L-3 对齐实测 2026-09-03）；非法流转拦截；状态变更留痕〔R-312, R-322〕 | order.feature | DRAFTED |
| FEAT-016 | 微信线上支付 | 线上收款 | P0 | 015 | V3 下单；回调验签+金额校验+幂等（重复通知不重复入账）；三段式事务〔模式手册 P2/P3〕 | payment.feature | DRAFTED |
| FEAT-017 | 人工收款确认 | 线下收款 | P0 | 015 | 待人工确认态；操作人+凭证留痕；确认后等同 paid；活动单确认时限 min(48h, 开始时间)〔R-320〕 | payment.feature | DRAFTED |
| FEAT-018 | 线下收款登记 | 支付宝/刷卡/转账 | P1 | 017 | 登记收款方式+金额+凭证图；进人工确认流 | payment.feature | DRAFTED |
| FEAT-019 | 僵尸单清理 | 资金对账 | P1 | 015 | 定时任务扫超时未支付；幂等；释放占用（活动名额等）〔模式手册 P5〕 | order.feature | DRAFTED |
| FEAT-020 | 用户端退款申请 | 家长自助 | P0 | 015 | 7 态状态机；服务端算可退金额（前端禁自算）；可退 0 禁提交；同订单单申请；pending 可撤销；被拒可再申请〔R-308〕 | refund.feature | DRAFTED |

## F3 会员与押金（identity+billing，对应 P4）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-021 | 会员状态机 | 生命周期核心 | P0 | 015 | 6 态（none/observation/pending_evaluation/formal/expired/withdrawn）+ withdrawn→observation/formal 重新入会边；转移矩阵穷举测试〔R-301〕 | membership.feature | DRAFTED |
| FEAT-022 | 观察期购买与到期 | 入会主路径 | P0 | 021 | 500 元/月；到期自动 pending_evaluation（权益保留）；无时限；转正=评估+年费〔V1.1 §3.3〕 | membership.feature | DRAFTED |
| FEAT-023 | 年费与二孩 9 折 | 定价规则 | P0 | 021 | 6000/5400；下单时账号下另有在会孩子即自动 9 折（观察期/待评估/正式都算）；不打折项明确；不叠加不追回〔V1.1 §3.1〕 | membership.feature | DRAFTED |
| FEAT-024 | 续费与到期提醒 | 留存 | P1 | 021 | 提前续费原到期日+365；过期续费付款日+365；30/14/7/当天四档提醒〔V1.1 §3.4〕 | membership.feature | DRAFTED |
| FEAT-025 | 押金状态机 | 资金安全 | P0 | 015 | unpaid/paid/partially_deducted/fully_deducted/refunding/refunded；按孩子独立；转移矩阵〔R-312〕 | deposit.feature | DRAFTED |
| FEAT-026 | 押金扣除与赔偿 | 遗失损坏处理 | P0 | 025 | 原价赔偿优先扣本人押金；不够扣记待结清持续提醒；先协商后赔偿；找回恢复上架〔V1.1 §3.6〕 | deposit.feature | DRAFTED |
| FEAT-027 | 押金补缴 | 恢复全额 | P0 | 025 | 余额<全额时可补缴=deposit_amount−available；补后状态回 paid；流水记录；退会退 available 不退 deducted〔R-312〕 | deposit.feature | DRAFTED |
| FEAT-028 | 退会申请 | 生命周期终点 | P0 | 021 | 6 态状态机；前置校验（无借阅/逾期/未结赔偿/进行中申请）；锁定范围；超管审核；结算三笔退款〔R-311〕 | withdrawal.feature | DRAFTED |
| FEAT-029 | 会员费退款联动退会 | 退款即退会 | P0 | 020,028 | observation/formal 退款=同时建退会申请+锁定；成功前再校验；成功后 withdrawn+自动发起押金退款；观察期用尽可退 0 禁提交〔R-309, R-310〕 | refund.feature | DRAFTED |
| FEAT-030 | 重新入会 | 回流客户 | P1 | 021 | withdrawn→observation（观察期单支付）/→formal（年费单支付或转让通过）；历史词数/积分/等级/打卡全保留〔R-301〕 | membership.feature | DRAFTED |

## F4 借阅与预约（circulation，对应 P5）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-031 | 到店借书 | 核心业务 | P0 | 021,025 | 识别学生（二维码/姓名/手机号）→锁主体行→校验（会员/押金/额度/副本状态/重复借）→副本行锁+原子扣减；AR 超范围提示不拦截；并发防超借 MySQL 实证〔模式手册 P10/P11〕 | borrowing.feature | DRAFTED |
| FEAT-032 | 还书 | 闭环 | P0 | 031 | 扫码识别借阅人+逾期提示；确认状态：正常/转维护/标记遗失；不影响阅读线 | borrowing.feature | DRAFTED |
| FEAT-033 | 续借 | 服务 | P0 | 031 | 每本 1 次延长 7 天（原到期日+7）；未逾期可自助；逾期书禁续借（馆员人工办留痕也不可破 1 次上限）〔V1.1 §5.1〕 | borrowing.feature | DRAFTED |
| FEAT-034 | 逾期扣减 | 温和治理 | P0 | 031 | 每逾期 1 本上限减 1（最低 0）；不扣积分不冻结；逾期名单催还；小程序预约/续借自动拦截〔V1.1 §5.4〕 | borrowing.feature | DRAFTED |
| FEAT-035 | 人工放行留痕 | 线下兜底哲学 | P0 | 031 | 异常提示+馆员可放行+必填原因+记录操作人/时间/异常快照；只适用线下操作；线上自助硬校验无人可放行〔红线：人工放行边界〕 | borrowing.feature | DRAFTED |
| FEAT-036 | 小程序预约 | 到店前锁书 | P0 | 031 | 校验（有效会员+押金+无逾期+额度）；reserved 锁 72h（占额度=在借+预约≤30）；到期自动释放+通知；家长可取消；同孩子同书单预约；副本异常自动换/转异常〔V1.1 §5.5〕 | reservation.feature | DRAFTED |
| FEAT-037 | 未入会临时借书开关 | 体验家庭 | P1 | 031,004 | 默认关；开启后限 1 本/72h 归还或入会/仅超管放行留痕/生成入会跟进〔R-313〕 | borrowing.feature | DRAFTED |

## F5 音频播放与阅读进度（reading，对应 P6）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-038 | 在线播放 | 听读入口 | P0 | 011,021 | 权限：有效会员全馆在架；过期仅在手；未入会无；5 档倍速；进度跨设备记忆〔V1.1 §6.1〕 | audio_reading.feature | DRAFTED |
| FEAT-039 | 完播判定（95% 防刷） | 数据可信核心 | P0 | 038 | 播放区间并集覆盖 ≥95%；seek 跳过不计；重复段不重复计；服务端校验覆盖增速 ≤Δt×2.0×1.2；2 倍速合法〔红线：音频完播防刷〕 | audio_reading.feature | DRAFTED |
| FEAT-040 | 打卡 | 习惯激励 | P0 | 039 | 当天首次读完即打卡（与 Quiz 无关）；同书不重复；自然日连续；中断清零已发不回收；跨零点按判定时刻〔V1.1 §6.3〕 | audio_reading.feature | DRAFTED |
| FEAT-041 | 阅读时长 | 报告数据 | P0 | 039 | 首次读完记音频原始时长（2 倍速听 30min 记 30min）；同书只记一次 | audio_reading.feature | DRAFTED |

## F6 Quiz 与有效词数（reading，对应 P7）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-042 | Quiz 解锁 | 读完即测 | P0 | 039 | 95% 达成自动解锁；不等还书；借书期间可测〔红线：还书≠解锁〕 | quiz.feature | DRAFTED |
| FEAT-043 | Quiz 提交与计分 | 公平性 | P0 | 042 | 5 题答对 4 过（80%）；终身 3 次提交（重借不重置）；未提交退出不占次数；取历史最高分；3 次用尽=failed〔红线：Quiz 公平性〕 | quiz.feature | DRAFTED |
| FEAT-044 | 题目快照 | 成绩可追溯 | P0 | 043 | 提交时保存题目+选项+答案快照；后续改题不影响历史成绩 | quiz.feature | DRAFTED |
| FEAT-045 | 管理员重置次数 | 特殊救济 | P1 | 043 | 仅超管；留痕；重置后重测通过正常计词数；测验积分只发一次 | quiz.feature | DRAFTED |
| FEAT-046 | 有效词数入账 | 激励基石 | P0 | 043 | Quiz 通过才计入；唯一约束（学生×书目终身一次）；永不回收；同事务驱动积分折算+等级进度+升级通知〔红线：有效词数〕 | words_ledger.feature | DRAFTED |

## F7 等级积分榜单（growth，对应 P8）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-047 | 等级 A-Z | 长期成长 | P0 | 046 | 每级 100 本（配置）；只升不降；触发书计旧级；升级同事务+通知；Z 封顶继续累计本数；初始 A/0〔R-306〕 | level.feature | DRAFTED |
| FEAT-048 | 等级配置变更重算 | 运营弹性 | P1 | 047 | 调低触发全量重算（幂等/只升不降/可查询/留痕）；调高不降级、进度保留封顶新阈−1〔R-307〕 | level.feature | DRAFTED |
| FEAT-049 | 积分三通道 | 行为激励 | P0 | 046 | 词数 100 词=1 分（零头池）；首过 +5/满分 +10 互斥只发一次；连打 7 天 +10/30 天 +50 循环；不扣/非负/永久/不可转赠/明细可见〔V1.1 §7.2〕 | points.feature | DRAFTED |
| FEAT-050 | 五榜单 | 社交激励 | P0 | 046 | 周/月/年/总/进步；总榜历史荣誉（含过期退会标"历史学员"，无详情跳转）；进步榜周增量 ≥100 才上；周期榜仅有效会员；英文名/昵称+头像；全员展示不可关闭〔R-317, R-318〕 | leaderboard.feature | DRAFTED |
| FEAT-051 | 里程碑 | 荣誉节点 | P0 | 046 | 默认 10万/50万/100万/500万/1000万/5000万（配置）；达成发勋章+通知家长馆员；永不回收 | milestone.feature | DRAFTED |

## F8 报告护照生词本（growth+reading，对应 P9）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-052 | 阅读护照 | 成就主页 | P0 | 047,051 | 词数/等级/勋章/里程碑/最近读完；退会只读历史 | passport.feature | DRAFTED |
| FEAT-053 | 周报月报图片 | 家长传播 | P1 | 046 | 周一/月初自动生成图片；可保存转发 | report.feature | DRAFTED |
| FEAT-054 | 生词本 | 语境习得 | P0 | 038 | 主动查词自动收录；记来源书目；同词唯一；可删除；查词不中断播放〔V1.1 §8.1〕 | vocabulary.feature | DRAFTED |
| FEAT-055 | 查词（ECDICT） | 听读辅助 | P0 | 054 | 340 万词库精确查询；音标+释义；上屏播放下屏查询 | vocabulary.feature | DRAFTED |
| FEAT-056 | 收藏夹 | 想读清单 | P1 | 007 | 学生×书目唯一；不限量；不占额度；下架书可见标"已下架"；退会只读〔R-314〕 | favorites.feature | DRAFTED |

## F9 线下活动（activity，对应 P10）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-057 | 活动发布 | 运营入口 | P0 | 004 | 类型/时间/地点/名额/费用（免费或定价）/限制/截止；免费+收费并存 | activity.feature | DRAFTED |
| FEAT-058 | 活动报名 | 名额管理 | P0 | 015,057 | 每孩子单独报名占名额；同活动同孩子唯一；9 态状态机（R-322）；待支付先占名额超时释放；满员关闭；并发扣减防超卖 | activity.feature | DRAFTED |
| FEAT-059 | 活动签到 | 到场闭环 | P0 | 058 | 电子入场券二维码；记录时间+操作人；签到影响退款资格 | activity.feature | DRAFTED |
| FEAT-060 | 活动退款 | 规则矩阵 | P0 | 020,058 | 已签到不退；未签到未开始全额；开始前 2h 关线上；取消逐单超管审核；改期保留名额可退〔V1.1 §9.3, R-322〕 | activity.feature | DRAFTED |
| FEAT-061 | 99 元首场资格 | 获客闭环 | P0 | 015 | 每账号一次；存在未全额退的已付 99 元单则不可再购；退款中不恢复；全额退且未参加才恢复〔R-321〕 | activity.feature | DRAFTED |
| FEAT-062 | 活动提醒 | 出席率 | P1 | 057 | 前 3/2/1 天+当天；发送状态记录 | notification.feature | DRAFTED |

## F10 权益转让与评估（identity，对应 P11）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-063 | 转让申请（16 条件） | 二孩家庭 | P0 | 021 | 同账号；转出 formal+剩余>0+无借阅/逾期/遗失/损坏/待结清/进行中申请/锁定；受让 none/withdrawn+无进行中；前端显示具体失败原因〔R-302, R-303〕 | transfer.feature | DRAFTED |
| FEAT-064 | 转让锁定 | 状态一致性 | P0 | 063 | 双方锁定：禁借/约/续/退款/退会/档案删除/新订单/新转让/受让/改到期日；允许查看+（不应发生的）还书；拒绝/撤销/超时/通过即解锁〔R-304〕 | transfer.feature | DRAFTED |
| FEAT-065 | 转让审核事务 | 资金安全 | P0 | 063 | 仅超管；72h 超时自动 expired+解锁+通知；通过时二次校验 6 项；同一事务 12 步（申请 approved/转出 withdrawn+退会记录/押金退款申请自动发起/受让 formal+到期日继承/转让记录/解锁/通知）〔R-305〕 | transfer.feature | DRAFTED |
| FEAT-066 | 观察期评估报告 | 专业服务 | P1 | 022 | 馆员上传图片（≤9 张）；家长孩子可见；上传留痕 | observation_report.feature | DRAFTED |

## F11 通知任务看板（admin，对应 P12 横切）

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-067 | 通知中心双通道 | 触达 | P0 | — | 站内必达+微信订阅尽力；发送状态/失败原因记录；场景全集（付款/退款/借还/到期/测验/里程碑/活动/预约/报告/转让/评估）〔R-319〕 | notification.feature | DRAFTED |
| FEAT-068 | 定时任务集 | 自动化 | P0 | — | 13+ 任务：转让超时/审核超时提醒/99 元 90 天提醒/押金不足/等级重算/活动取消退款检查/预约释放/逾期标记/僵尸单/周月报生成/待评估提醒/勋章颁发；全量幂等+失败重试+告警〔R-319〕 | scheduler.feature | DRAFTED |
| FEAT-069 | 数据看板 | 经营视图 | P1 | 各域 | 实时（藏书/借还/逾期）+汇总（热门/占比/续费率/通过率）；Excel 导出 | dashboard.feature | DRAFTED |
| FEAT-070 | 未缴费权限矩阵执行 | 边界执法 | P0 | 各域 | 按 R-313 矩阵逐行执行（音频禁/Quiz 禁/榜单禁/借阅硬拦截…）；矩阵驱动的集成测试 | permission_matrix.feature | DRAFTED |

## F12 运营审核工作台（admin 横切，对应 WM13，2026-08-31 增补）

> 来源：馆长 2026-08-31 裁决增补（PRD §2.4 / 规划提案 / 任务包 v2）。核心设计：显示态实时算、审计态事件写。

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-071 | 管理待办收件箱 | 运营感知 | P0 | F2/F3/F10 | 六类申请事件（退款/退会/转让/活动批量退款/转让临超时/退款执行失败）同事务落 admin_notification，dedup 幂等；通知中心双页签（家长通知+管理待办）；三态显示（待处理/已审结/已失效·注明原因）实时跟随业务单据；通知带申请原因原文；"去处理"直达单据并高亮 | admin_todo.feature | DRAFTED |
| FEAT-072 | 待办感知聚合 | 触达 | P0 | FEAT-071 | todo-counts 单请求聚合（禁 N 次）；侧边栏徽标+仪表盘待办卡同源；审核完成主动刷新（不等轮询）；拉取失败禁显 0；权限粒度：审核类仅超管、待确认收款跟 member.manage | admin_todo.feature | DRAFTED |
| FEAT-073 | 终态回写与超时预警 | 闭环 | P0 | FEAT-071 | 8 条终态路径（审核×6/家长撤销×3/转让超时）回写审计字段；转让临超时定时任务（每单 dedup 一次）注册任务看板；手动标记已处理必填原因+审计留痕 | admin_todo.feature | DRAFTED |

## F13 会员档案管理增强（WM3 插修 1，2026-09-01 用户验收拍板增补）

> 来源：WM3 手动验收反馈（用户拍板：编辑删除守卫口径 / 凭证做 / 字段全开）。
> PRD 变更以书面变更需求为准——本段为 03 清单增补，PRD 定稿正文不动。

| ID | 名称 | 价值 | 优先级 | 依赖 | 验收标准 | Gherkin | 状态 |
|---|---|---|---|---|---|---|---|
| FEAT-074 | 家长/孩子编辑+删除（订单守卫） | 运营修正 | P1 | F2 | 编辑：家长姓名/手机号（唯一 422+登录标识提示）/孩子姓名/英文名/性别/生日/年级；删除：软删（家长连带名下孩子）；守卫双保险：孩子存在未删订单（任意状态含 cancelled）→ 409 禁改禁删，家长名下任一孩子有订单 → 409；UI 按钮禁用+tooltip；家长 tab（N）含孩子数/守卫标志〔R-315 解禁口径〕 | wm3_member_edit.feature | IMPLEMENTED |
| FEAT-074 变更记录 1 | 守卫口径细化（F7，用户拍板 2026-09-01） | — | — | — | 有订单孩子仅身份字段（姓名/性别/生日）禁改 409；学籍动态字段（英文名/年级/AR）放开可改（年级随学年变化）；删除守卫不变；家长守卫不变（家长编辑的即身份字段） | wm3_member_edit.feature @wm3-f7 | IMPLEMENTED |
| FEAT-075 | 收款凭证上传与查看 | 资金留痕 | P1 | F2 | 待确认订单上传凭证图（image/*，可选传）；确认收款带 voucher_path；已支付订单可查看凭证大图（member.manage 权限独立通道，勿挂 book.manage uploads）；家长 token 401 回归〔R-316 模式复用〕 | —（B2 单测为主） | IMPLEMENTED |
| FEAT-076 | 小程序「我的订单」页（每笔钱完整账本） | 资金可见性 | P1 | F2 | order-history 页（历史幽灵页落地）：孩子选择+订单列表（类型徽标/订单号/金额强字/状态中文/支付时间）；每行 paid 态「申请退款」快捷入口跳 refund-apply；member 页菜单入口；后端 my-orders 或_ 查询（孩子单 ∪ 家长级单 child_id NULL，漏查会丢家长级活动费单）；空态 | —（X7 单测 9 例） | IMPLEMENTED |
| FEAT-077 | 退会审核预估结算（盲批→明批） | 资金安全 | P0 | F2 | GET /withdrawals/{id}/settle-preview（super_admin，仅 normal+applying；联动单 422）：明细行（kind/order_no/amount/rule）+押金余额+合计；审核弹窗展示；**preview 与 review 调同一份 _settle_items（同源，灵魂断言：preview total == review 实际退款单合计，防两套公式漂移——WM5 上限公式两套前车之鉴）**〔R-311 结算段〕 | —（X2 单测含一致性断言） | IMPLEMENTED |
| FEAT-078 | 可退金额三形态展示 + 0 元拦截 | 资金可见性 | P1 | F2 | 小程序可退卡片三形态：proportional（实付 · 已用 N/总天 → 预计可退，比例退折算过程可见）/ full（全额徽标）/ zero（不可退+原因）；0 元禁提交三层守卫（后端 422「该订单当前无可退金额」/前端按钮置灰+提示/前置校验双保险）——0 元申请会联动建退会单+锁孩子，审核员误批即 withdrawn（真业务陷阱） | —（X6+R1 单测） | IMPLEMENTED |
| FEAT-079 | 小程序退款申请撤销入口 | 用户自主权 | P1 | F2 | refund-apply 退款记录行 pending 态「撤销申请」按钮（wx.showModal 确认→cancelRefund→列表刷新）；后端 cancel 端点本就绪（仅 pending 可撤，撤后 cancelled+订单恢复）；管理端 todo 显示「已失效·家长已撤销」已有（WM13 S1 灵魂设计的前端断链修复） | refund.feature（存量 @draft）；R1/X5 pytest 兜底 | IMPLEMENTED |
| FEAT-080 | 管理端订单类型扩展 | 资金动线 | P1 | 004,015 | 管理端创建订单六类：现有三类+押金（deposit_amount 配置，confirm 激活 Deposit+Ledger）+活动（activity_id 必填带出 fee，不联动报名——边界默认值）+自定义（说明+金额，纯资金流水不参与会员计算，可走退款链）〔PRD §3.5.2〕 | order_types.feature（四场景 16 steps） | IMPLEMENTED |
| FEAT-081 | 儿童测验成就徽章（详情页三态） | 儿童激励 | P2 | 17 号插修8 | book-detail 测验区三态徽章：金卡 PASSED（🏆+金橙渐变+星级档位+最佳成绩+鼓励语随机，点击→quiz-result 成绩单）/蓝卡再挑战（剩余次数+最佳分+脉冲按钮，attempts_used=0 文案自适应"开始挑战"）/灰态锁定引导（locked 含已听百分比、failed 提示找老师重置）；数据源 getQuiz 现成字段零后端改动，onLoad/onShow 双接线（测完返回徽章即时切换）；书架🏆角标需后端批量接口挂第四批；reader 完播弹窗文案同批统一 | 纯 miniapp 前端（node --check+C 级 gate 基线持平）；真机目视 | IMPLEMENTED |

随修项（bugfix/小缺口，不单开 FEAT，WM3 插修 1）：A1 计数懒加载守卫（WM13-F4 同族）、
A2 创建时间字段错配、A3 refunded 标签、A4 筛选「全部」、A5 订单关键字搜索、
B4 建档家长默认最新+前 5、D1 待确认红底白字、D2 临期演示数据、D3 文档笔误。

随修项（WM3 插修 2，首轮目视反馈）：F1 家长 tab 计数守卫（同族第三犯，E-20260901-03）、
F2 家长 tab create_time 后端补齐、F3 编辑弹窗重复 grade 字段（复制粘贴事故）、
F4 孩子列表性别/年级/AR 三列（用户拍板）、F5 凭证/评估报告 Upload onPreview 本地
Modal 预览（禁 antd 默认 window.open 空白页）、F6 client.ts 基建双修（FormData 禁设
Content-Type + 422 detail 数组拼接禁 [object Object]）、F8 家长编辑按钮 disabled
一致化、F7 已升 FEAT-074 变更记录 1。

随修项（WM13 插修 3，验收反馈）：W1 RefundCenter tab 双计数（待审 N · 待执行/退款中
M，M>0 红字）、W2 通知列表 60s 轮询（页面激活时）、W3 凭证预览限高 70vh 整屏、
W4 验收动线 xlsx 修正（8888 名下零订单→改 7777 临期孩）。

随修项（插修 4，退会走查反馈）：X1 退会 tab 审核断链【P0】（操作列 pending vs
applying 一词之差，直接退会永不可审；同批 L136 tab 计数同修）、X3 执行退款 toast
带金额、X4 通知轮询挂载隐藏边界、X5 seed 已付订单补 paid_at 错开天数（比例退可见）。

基建随修（不占 FEAT）：gate.sh 防呆归档（E-20260901-04，GATE_LOG_NAME 自动 mkdir+tee）
+ 单测/覆盖率双跑合并（-42% 门禁时长，2026-09-02 裁定见 docs/09 §九）、
admin 改密 bump token_generation（WM1 遗留"改密踢旧 token"文档化属性落地）、
checkout Reservation 锁定读补 populate_existing（P1 遗留）、vite host 局域网验收、
词库开发期裁剪（335.7 万行备份 ~/dmkwords-backups/，上线导回）。

## 需求锚点对照（V1.0.1 → Feature 映射）

| V1.0.1 规则 | 覆盖 Feature |
|---|---|
| R-300 执行总原则 | 全部（宪法级） |
| R-301 会员状态机补充 | FEAT-021, 030 |
| R-302/303/304/305 权益转让 | FEAT-063, 064, 065 |
| R-306/307 等级工程化 | FEAT-047, 048 |
| R-308/309/310 退款联动 | FEAT-020, 029 |
| R-311 退会申请 | FEAT-028 |
| R-312 押金补缴 | FEAT-025, 027 |
| R-313 未缴费权限矩阵 | FEAT-070 |
| R-314 收藏夹 | FEAT-056 |
| R-315 档案删除本期不做 | **2026-09-01 用户拍板解禁管理端**：编辑+删除（无订单守卫口径）→ FEAT-074（家长端入口仍不做，PRD §23 保留；变更依据=用户 2026-09-01 验收反馈原话） |
| R-316 封面路径 | FEAT-010, 011 |
| R-317 总榜 | FEAT-050 |
| R-318 进步榜 | FEAT-050 |
| R-319 通知任务配置 | FEAT-067, 068 |
| R-320 活动人工确认 | FEAT-017 |
| R-321 99 元资格 | FEAT-061 |
| R-322 活动报名状态机 | FEAT-058 |
| R-323 数据模型清单 | 各域 models（开发期落） |
| PRD §2.4 运营审核工作台（2026-08-31 增补） | FEAT-071, 072, 073 |

## 变更流程

需求变更 → 本清单登记（Feature 增/改/删）→ rg 冲突检查（同口径全库）→ Gherkin 局部更新 → 台账记录。清单与 Gherkin 的状态字段同步更新，禁止跳过清单直接改场景。
