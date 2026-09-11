// miniapp/utils/api.js — WM6 家长端 API 层（新契约 /api/miniapp/*）
const req = require('./request')

module.exports = {
  // 登录（开发期：手机号 + 验证码 1234；上线前接微信 code2session）
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
  myOrders(childId) {
    return req.get('/api/miniapp/orders', null, { params: { child_id: childId } })
  },
  refundPreview(childId, orderId) {
    return req.get('/api/miniapp/refund-preview', null, { params: { child_id: childId, order_id: orderId } })
  },
  applyRefund(childId, orderId, reason) {
    return req.post('/api/miniapp/refund-requests', { child_id: childId, order_id: orderId, reason })
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
  myWithdrawals(childId) {
    return req.get('/api/miniapp/withdrawals', null, { params: { child_id: childId } })
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
  listActivities(childId) {
    return req.get('/api/miniapp/activities', null, { params: { child_id: childId } })
  },
  activityDetail(activityId, childId) {
    return req.get(`/api/miniapp/activities/${activityId}`, null, { params: { child_id: childId } })
  },
  enrollActivity(activityId, childId) {
    return req.post(`/api/miniapp/activities/${activityId}/enroll`, { child_id: childId })
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
  listVocabulary(childId) {
    return req.get('/api/miniapp/vocabulary', null, { params: { child_id: childId } })
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

  continueListening(childId) {
    return req.get('/api/miniapp/continue-listening', null, { params: { child_id: childId } })
  },

  // 打卡
  getCheckins(childId, days) {
    return req.get('/api/miniapp/checkins', null, { params: { child_id: childId, days: days || 60 } })
  },

  // 预约
  listReservations(childId) {
    return req.get('/api/miniapp/reservations', null, { params: { child_id: childId } })
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
  notifications(page = 1, pageSize = 20, category = '') {
    const params = { page, page_size: pageSize }
    if (category) params.category = category
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
