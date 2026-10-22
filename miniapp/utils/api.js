// miniapp/utils/api.js — WM6 家长端 API 层（新契约 /api/miniapp/*）
const req = require('./request')

/**
 * 列表接口统一入口。
 *
 * `opts.meta === true` 时返回 `{ rows, meta }`——`meta` 是后端在**响应头**里给的截断信号
 * （`X-List-Limit` / `X-List-Truncated`，P2-44）。为什么要有：列表类接口有硬上限
 * （家长订单 50、预约含历史 50、活动 50…），而响应体就是裸数组——页面看到 50 行会渲染成
 * "这就是全部"，于是家长以为自己只有 50 条订单。需要提示条的页面传 `meta: true`，
 * 其余调用点不传 ⇒ 行为与形状完全不变。
 */
function listCall(path, options, opts) {
  if (!opts || !opts.meta) return req.get(path, null, options)
  return req.get(path, null, { ...options, withHeaders: true }).then((r) => ({
    rows: r.data,
    meta: req.listMeta(r.headers),
  }))
}

module.exports = {
  // 登录（2026-10-08 接线）：微信一键登录为主，短信验证码为兜底
  // ① 微信主通道：wx.login 拿 code → openid 已绑 → 直接登录态；未绑 → need_bind + bind_ticket
  // 登录选项（2026-10-12 拍板 P1）：服务端下发短信通道开关 → 关闭时隐藏手机号入口
  getLoginOptions() {
    // P3（第一百一十三批）：`get(path, data, options)` 三参——`{auth:false}` 原先落在 **data 槽**，
    // 被拼成 `?auth=false`，而 `auth` 实际仍是默认 true（有 token 时照样带 Authorization）。
    // 端点本身公开，故无功能故障，但意图未生效。
    return req.get('/api/miniapp/login/options', null, { auth: false })
  },
  // 业务口径下发（第八轮 E 批剩余）：预约保留小时/完播阈值/观察期/短信重发间隔等**展示口径**
  // 一律读这里，前端不再自持数字副本（改配置 → 文案跟随）。公开端点：登录页倒计时也要用。
  getConfigs() {
    return req.get('/api/miniapp/configs', null, { auth: false })
  },
  loginByWechat(wxCode) {
    return req.post('/api/miniapp/login/wechat', { code: wxCode }, { auth: false })
  },
  // ② 首次绑定：绑定凭证 + 手机号 + 短信码（手机号必须已在馆建档）
  bindByWechat(bindTicket, phone, code) {
    return req.post('/api/miniapp/login/bind', { bind_ticket: bindTicket, phone, code }, { auth: false })
  },
  // ③ 短信兜底 / 绑定共用：发验证码（同号重发间隔由配置 sms_send_interval_seconds 定、
  //    每日上限在服务端；倒计时读 getConfigs() 下发值，见 utils/configs.js）
  sendSms(phone, purpose) {
    return req.post('/api/miniapp/sms/send', { phone, purpose: purpose || 'login' }, { auth: false })
  },
  // 短信验证码登录（开发期固定码 1234 仍可用；生产走真短信）
  login(phone, code) {
    return req.post('/api/miniapp/login', { phone, code }, { auth: false })
  },

  // 书目
  getBookDetail(bookId) {
    return req.get(`/api/miniapp/books/${bookId}`)
  },
  listBooks(params = {}) {
    const q = {
      keyword: params.keyword || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    }
    if (params.grade) q.grade = params.grade
    if (params.topic) q.topic = params.topic
    if (params.ar_min !== undefined && params.ar_min !== null && params.ar_min !== '') q.ar_min = params.ar_min
    if (params.ar_max !== undefined && params.ar_max !== null && params.ar_max !== '') q.ar_max = params.ar_max
    if (params.has_audio) q.has_audio = true
    if (params.sort && params.sort !== 'newest') q.sort = params.sort
    return req.get('/api/miniapp/books', null, { params: q })
  },

  // 阅读进度
  getProgress(bookId, childId) {
    return req.get(`/api/miniapp/books/${bookId}/progress`, null, {
      params: { child_id: childId },
    })
  },
  // 防刷心跳（PRD R-151：每 10 秒；暂停/seek/退出/切倍速时也上报）
  reportProgress(childId, bookId, position, sessionStart) {
    return req.post('/api/miniapp/reading/progress', {
      child_id: childId,
      book_id: bookId,
      position: Math.floor(position),
      session_start: sessionStart === null || sessionStart === undefined ? null : Math.floor(sessionStart),
    })
  },

  // 测验与成长（WM7）
  getQuiz(bookId, childId) {
    return req.get(`/api/miniapp/quiz/${bookId}`, null, { params: { child_id: childId } })
  },
  submitQuiz(bookId, childId, answers) {
    return req.post(`/api/miniapp/quiz/${bookId}/submit`, { child_id: childId, answers })
  },
  growthSummary(childId) {
    return req.get('/api/miniapp/growth/summary', null, { params: { child_id: childId } })
  },
  pointsList(childId) {
    return req.get('/api/miniapp/points', null, { params: { child_id: childId } })
  },
  // 已通过测验的书（书架「已通过」页签）
  passedBooks(childId) {
    return req.get('/api/miniapp/growth/passed-books', null, { params: { child_id: childId } })
  },

  // 榜单 / 护照 / 报告（WM8）
  leaderboard(period, childId) {
    return req.get('/api/miniapp/leaderboard', null, { params: { period, child_id: childId } })
  },
  passport(childId) {
    return req.get('/api/miniapp/passport', null, { params: { child_id: childId } })
  },
  report(kind, childId) {
    return req.get(`/api/miniapp/reports/${kind}`, null, { params: { child_id: childId } })
  },
  reportImageUrl(kind, childId) {
    const token = wx.getStorageSync('token')
    const app = getApp()
    return `${app.globalData.baseURL}/api/miniapp/reports/${kind}/image?child_id=${childId}&token=${encodeURIComponent(token)}`
  },

  // 退款 / 退会 / 转让 / 评估报告（WM10）
  // `opts.meta` → `{rows, meta}`（订单列表有 50 条硬上限，`meta.truncated` 为真即"还有更多"）
  myOrders(childId, opts) {
    return listCall('/api/miniapp/orders', { params: { child_id: childId } }, opts)
  },
  // 线上支付（WM12-A）：价格下发 → 下单 → 发起支付（返回 wx.requestPayment 参数）
  getPaymentPlans(childId) {
    return req.get('/api/miniapp/payment/plans', null, { params: { child_id: childId } })
  },
  createOnlineOrder(childId, orderType) {
    return req.post('/api/miniapp/orders', { child_id: childId, order_type: orderType })
  },
  payOrder(orderId) {
    return req.post(`/api/miniapp/orders/${orderId}/pay`, {})
  },
  refundPreview(childId, orderId) {
    return req.get('/api/miniapp/refund-preview', null, { params: { child_id: childId, order_id: orderId } })
  },
  applyRefund(childId, orderId, reason) {
    return req.post('/api/miniapp/refund-requests', { child_id: childId, order_id: orderId, reason })
  },
  // P1-11（2026-10-09）：退出登录 = 服务端撤销（token 代数 +1，旧 token 全部失效）
  logout() {
    return req.post('/api/miniapp/logout', {})
  },
  myRefunds(childId) {
    return req.get('/api/miniapp/refund-requests', null, { params: { child_id: childId } })
  },
  // W5 撤销断链修复：后端 POST /refund-requests/{id}/cancel 全就绪（仅 pending 可撤）
  cancelRefund(requestId, childId) {
    return req.post(`/api/miniapp/refund-requests/${requestId}/cancel`, { child_id: childId })
  },
  applyWithdrawal(childId, reason) {
    return req.post('/api/miniapp/withdrawals', { child_id: childId, reason })
  },
  // 本次退会「能退哪些费用」（审核通过后由后端返回）
  withdrawalSettlement(requestId, childId) {
    return req.get(`/api/miniapp/withdrawals/${requestId}/settlement`, null, {
      params: { child_id: childId },
    })
  },
  myWithdrawals(childId) {
    return req.get('/api/miniapp/withdrawals', null, { params: { child_id: childId } })
  },
  // E-20260912-11：撤销进行中的退会申请（applying → cancelled；后端早就绪，此前无前端调用）
  cancelWithdrawal(requestId, childId) {
    return req.post(`/api/miniapp/withdrawals/${requestId}/cancel`, { child_id: childId })
  },
  transferConditions(sourceChildId, targetChildId) {
    return req.get('/api/miniapp/transfers/conditions', null, {
      params: { source_child_id: sourceChildId, target_child_id: targetChildId },
    })
  },
  applyTransfer(sourceChildId, targetChildId) {
    return req.post('/api/miniapp/transfers', { source_child_id: sourceChildId, target_child_id: targetChildId })
  },
  myTransfers() {
    return req.get('/api/miniapp/transfers')
  },
  cancelTransfer(transferId) {
    return req.post(`/api/miniapp/transfers/${transferId}/cancel`)
  },
  observationReports(childId) {
    return req.get('/api/miniapp/observation-reports', null, { params: { child_id: childId } })
  },

  // 线下活动（WM9）
  // `opts.meta` → `{rows, meta}`（近期活动同样有 50 条硬上限）
  listActivities(childId, opts) {
    return listCall('/api/miniapp/activities', { params: { child_id: childId } }, opts)
  },
  activityDetail(activityId, childId) {
    return req.get(`/api/miniapp/activities/${activityId}`, null, { params: { child_id: childId } })
  },
  enrollActivity(activityId, childId) {
    return req.post(`/api/miniapp/activities/${activityId}/enroll`, { child_id: childId })
  },
  // 往期活动回顾（2026-09-20 C 批）：只读，不需要 child_id
  activitiesPast(limit = 30) {
    return req.get('/api/miniapp/activities/past', null, { params: { limit } })
  },
  myEnrollments(childId) {
    return req.get('/api/miniapp/enrollments', null, { params: { child_id: childId } })
  },
  cancelEnrollment(enrollmentId, childId) {
    return req.post(`/api/miniapp/enrollments/${enrollmentId}/cancel`, { child_id: childId })
  },
  refundApplyEnrollment(enrollmentId, childId) {
    return req.post(`/api/miniapp/enrollments/${enrollmentId}/refund-apply`, { child_id: childId })
  },

  // 生词本 / 收藏 / 书架（WM8）
  lookupWord(word, childId, bookId) {
    const params = { word, child_id: childId }
    if (bookId) params.book_id = bookId
    return req.get('/api/miniapp/vocabulary/lookup', null, { params })
  },

  // 评估报告图片 URL（query token：image 组件无法带头）
  observationImageUrl(relPath) {
    const token = wx.getStorageSync('token')
    const app = getApp()
    const sub = relPath.replace(/^observation\//, '')
    return `${app.globalData.baseURL}/api/miniapp/observation-images/${sub}?token=${encodeURIComponent(token)}`
  },
  // 生词本列表（P3 第一百一十六批：**分页**——原实现全表回吐 + 整表 setData）
  listVocabulary(childId, page = 1) {
    return req.get('/api/miniapp/vocabulary', null, { params: { child_id: childId, page } })
  },
  // 统计五口径（收录词数/累计查词/来源书目/本周新增/查过多次）由服务端聚合，前端不另算
  // （PRD §8.1.1 实现说明：列表一旦分页，前端 reduce 出来的数就与真实口径漂移）
  summaryVocabulary(childId) {
    return req.get('/api/miniapp/vocabulary/summary', null, { params: { child_id: childId } })
  },
  removeVocabulary(id, childId) {
    return req.del(`/api/miniapp/vocabulary/${id}`, null, { params: { child_id: childId } })
  },
  listFavorites(childId) {
    return req.get('/api/miniapp/favorites', null, { params: { child_id: childId } })
  },
  addFavorite(childId, bookId) {
    return req.post('/api/miniapp/favorites', { child_id: childId, book_id: bookId })
  },
  removeFavorite(bookId, childId) {
    return req.del(`/api/miniapp/favorites/${bookId}`, null, { params: { child_id: childId } })
  },
  // R3（插修 16）：播放入口前置预检（book-detail onPlay）
  audioPermission(childId, bookId) {
    return req.get('/api/miniapp/books/' + bookId + '/audio-permission', null, { params: { child_id: childId } })
  },

  // T45（FEAT-082）：首页轮播位（有封面 PUBLISHED 未开始 ≤5）
  activityCarousel() {
    return req.get('/api/miniapp/activities/carousel')
  },

  // T43（U2）：书架角标批量状态（3 次 IN 查询禁 N+1）
  quizStatusBatch(childId, bookIds) {
    return req.get('/api/miniapp/quiz/status-batch', null, { params: { child_id: childId, book_ids: bookIds.join(',') } })
  },

  currentBorrows(childId) {
    return req.get('/api/miniapp/borrows', null, { params: { child_id: childId } })
  },
  // 自助续借（PRD §5.1，2026-10-13 G6）：未逾期的书一键续借，从原到期日延长 renew_days 天
  renewBorrow(recordId, childId) {
    return req.post(`/api/miniapp/borrows/${recordId}/renew`, { child_id: childId })
  },

  continueListening(childId) {
    return req.get('/api/miniapp/continue-listening', null, { params: { child_id: childId } })
  },

  // 打卡
  // 打卡日历。
  // ① 默认：最近 `days` 条打卡记录（首页只要"今天打没打 + 连续几天"）；
  // ② `range = { startDate, endDate }`：按**日期段**取（日历页翻月——原先固定请求最近 90 条，
  //    往前翻超过窗口就一片空白，家长以为孩子那几个月没坚持，P3 第一百一十六批）。
  getCheckins(childId, days, range) {
    const params = { child_id: childId, days: days || 60 }
    if (range && range.startDate && range.endDate) {
      params.start_date = range.startDate
      params.end_date = range.endDate
    }
    return req.get('/api/miniapp/checkins', null, { params })
  },

  // 预约
  // `opts.includeClosed` = 连已结束（已核销/已取消/已超时）一起回 ——「我的预约」页用；
  // 默认只回锁定中（书架/首页/详情页口径，与后端默认一致）。
  // `opts.meta` → `{rows, meta}`（含历史时有 50 条上限）
  listReservations(childId, opts) {
    const { includeClosed = false } = opts || {}
    return listCall(
      '/api/miniapp/reservations',
      { params: includeClosed ? { child_id: childId, include_closed: true } : { child_id: childId } },
      opts
    )
  },
  createReservation(childId, bookId) {
    return req.post('/api/miniapp/reservations', { child_id: childId, book_id: bookId })
  },
  cancelReservation(reservationId, childId) {
    return req.post(`/api/miniapp/reservations/${reservationId}/cancel`, { child_id: childId })
  },

  // 押金（R-312 家长端）
  myDeposit(childId) {
    return req.get('/api/miniapp/deposits', null, { params: { child_id: childId } })
  },
  createSupplementOrder(childId) {
    return req.post('/api/miniapp/deposits/supplement-orders', { child_id: childId })
  },

  // 消息中心（WM11）
  // fix34 R0：scene 过滤——阅读圈顶部「谁赞了你」通知条只拉 circle.liked
  notifications(page = 1, pageSize = 20, category = '', scene = '') {
    const params = { page, page_size: pageSize }
    if (category) params.category = category
    if (scene) params.scene = scene
    return req.get('/api/miniapp/notifications', null, { params })
  },
  markNotificationsRead(ids = [], all = false) {
    return req.post('/api/miniapp/notifications/read', { ids, all })
  },

  // 家长资料（WM14-B：展示称呼）
  updateParentProfile(displayName) {
    return req.put('/api/miniapp/parent/profile', { display_name: displayName })
  },

  // 阅读圈（WM14-A）
  // fix33 R2：带 child_id=当前孩子——后端据此算 liked_by_me（兄弟状态互不串味）
  circlePosts(page = 1, pageSize = 10, childId = null) {
    return req.get('/api/miniapp/circle/posts', null, {
      params: { page, page_size: pageSize, child_id: childId },
    })
  },
  circleMyCards(childId) {
    return req.get('/api/miniapp/circle/my-cards', null, { params: { child_id: childId } })
  },
  circleShare(childId, cardType, refId) {
    return req.post('/api/miniapp/circle/posts', {
      child_id: childId, card_type: cardType, ref_id: refId,
    })
  },
  // fix33 R2：点赞主体=孩子（后端必填；缺 → 422「请先选择孩子」）
  circleLike(postId, childId) {
    return req.post(`/api/miniapp/circle/posts/${postId}/like`, { child_id: childId })
  },
  // 2026-09-16：家长名下孩子列表（**与登录载荷同源**）——供小程序刷新本地缓存。
  // 修「点赞头像 vs 我的页头像不一致」：后台改了孩子头像，本地快照必须能同步。
  myChildren() {
    return req.get('/api/miniapp/children')
  },
  // WM15-R3：孩子内置头像（白名单 id；空串=清空）
  updateChildAvatar(childId, avatar) {
    return req.put(`/api/miniapp/children/${childId}/avatar`, { avatar: avatar || '' })
  },
  // WM15-R4：孩子名片页（英文名/头像/成就数据 + 勋章墙 + TA 的帖子）
  circleChildProfile(childId) {
    return req.get(`/api/miniapp/circle/children/${childId}/profile`)
  },
  // WM15-R5：名片海报相对路径（前端自己拼 token——image 组件无法带头）
  circleChildPosterUrl(childId) {
    return `/api/miniapp/circle/children/${childId}/poster`
  },
  // fix33 R2/R3：取消点赞同样按孩子主体定位（DELETE 走 query——body 客户端不友好）
  circleUnlike(postId, childId) {
    return req.del(`/api/miniapp/circle/posts/${postId}/like`, null, {
      params: { child_id: childId },
    })
  },
  circleDeletePost(postId) {
    return req.del(`/api/miniapp/circle/posts/${postId}`)
  },
}
