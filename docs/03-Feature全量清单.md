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
| FEAT-015 | 订单模型与状态机 | 资金基石 | P0 | 004 | 5 类订单（first_activity/observation/formal/activity/deposit_supplement）× 7 态；非法流转拦截；状态变更留痕〔R-312, R-322〕 | order.feature | DRAFTED |
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
| R-315 档案删除本期不做 | 清单外（宪法范围纪律） |
| R-316 封面路径 | FEAT-010, 011 |
| R-317 总榜 | FEAT-050 |
| R-318 进步榜 | FEAT-050 |
| R-319 通知任务配置 | FEAT-067, 068 |
| R-320 活动人工确认 | FEAT-017 |
| R-321 99 元资格 | FEAT-061 |
| R-322 活动报名状态机 | FEAT-058 |
| R-323 数据模型清单 | 各域 models（开发期落） |

## 变更流程

需求变更 → 本清单登记（Feature 增/改/删）→ rg 冲突检查（同口径全库）→ Gherkin 局部更新 → 台账记录。清单与 Gherkin 的状态字段同步更新，禁止跳过清单直接改场景。
