// frontend/utils/api.js — API 接口层
const req = require('./request')

module.exports = {
  // 用户
  wxLogin(code) { return req.post('/user/wx-login', { code }, { auth: false }) },
  getUserInfo() { return req.get('/user/info') },

  // 孩子
  getChildren() { return req.get('/child/') },
  getTiers() { return req.get('/order/tiers') },
  getChild(id) { return req.get(`/child/${id}`) },
  createChild(data) { return req.post('/child/', data) },

  // 图书
  searchBooks(params) { return req.get('/book/search', null, { params }) },
  getBookDetail(id) { return req.get(`/book/${id}`) },
  getRelatedBooks(bookId, limit = 6) { return req.get(`/book/${bookId}/related`, null, { params: { limit } }) },

  // 书架
  getBookshelf(childId) { return req.get('/bookshelf/', null, { params: { child_id: childId } }) },
  addToShelf(bookId, childId) { return req.post('/bookshelf/', { book_id: bookId }, { params: { child_id: childId } }) },
  removeFromShelf(bookId, childId) { return req.del(`/bookshelf/${bookId}`, null, { params: { child_id: childId } }) },

  // 收藏夹（独立于书架想读清单）
  getFavorites(childId) { return req.get('/favorites/', null, { params: { child_id: childId } }) },
  addFavorite(bookId, childId) { return req.post('/favorites/', { book_id: bookId }, { params: { child_id: childId } }) },
  removeFavorite(bookId, childId) { return req.del(`/favorites/${bookId}`, null, { params: { child_id: childId } }) },

  // 阅读
  getBookPages(bookId, childId) { return req.get(`/reading/pages/${bookId}`, null, { params: { child_id: childId } }) },
  getProgress(bookId, childId) { return req.get(`/reading/progress/${childId}/${bookId}`) },
  saveProgress(childId, bookId, page, total) { return req.post('/reading/progress', { child_id: childId, book_id: bookId, current_page: page, total_pages: total }) },
  startSession(childId, bookId) { return req.post('/reading/session/start', { book_id: bookId, child_id: childId }) },
  endSession(sid, pages, words, minutes) { return req.put(`/reading/session/${sid}/end`, { pages_read: pages, words_read: words, reading_minutes: minutes }) },

  // 打卡
  getCheckinCalendar(childId, year, month) { return req.get(`/reading/checkin/${childId}`, null, { params: { year, month } }) },
  getCheckinRecords(childId) { return req.get(`/reading/checkin/${childId}/records`) },
  getStreak(childId) { return req.get(`/reading/streak/${childId}`) },

  // 借阅
  getChildBorrows(childId, status) { return req.get(`/borrow/${childId}`, null, { params: { status } }) },

  // 查词
  lookupWord(word) { return req.get(`/vocabulary/lookup/${word}`) },
  addToVocab(childId, word, bookId) { return req.post('/vocabulary/', { word, child_id: childId, book_id: bookId }) },
  getVocabList(childId, status) { return req.get(`/vocabulary/${childId}`, null, { params: { status } }) },
  getLearningWords(childId) { return req.get(`/vocabulary/${childId}/learning-words`) },
  getVocabStats(childId) { return req.get(`/vocabulary/${childId}/stats`) },
  markMastered(vocabId) { return req.put(`/vocabulary/${vocabId}/master`) },
  removeVocab(vocabId) { return req.del(`/vocabulary/${vocabId}`) },

  // 统计
  getStatsSummary(childId) { return req.get('/report/stats/summary', null, { params: { child_id: childId } }) },
  getTodayStats(childId) { return req.get('/report/stats/today', null, { params: { child_id: childId } }) },
  getTrend(childId, days) { return req.get('/report/stats/trend', null, { params: { child_id: childId, days: days || 7 } }) },
  getWeeklyReport(childId) { return req.get('/report/stats/weekly', null, { params: { child_id: childId } }) },

  // 排行榜
  getLeaderboard(period, levelId, limit) { return req.get('/advancement/leaderboard', null, { params: { period: period || 'total', level_id: levelId, limit } }) },

  // 晋级
  getLevels() { return req.get('/advancement/levels') },
  getCurrentLevel(childId) { return req.get(`/advancement/level/${childId}`) },

  // 测验
  startQuiz(bookId, childId) { return req.post('/advancement/quiz/start', { book_id: bookId }, { params: { child_id: childId } }) },
  getQuizQuestions(bookId) { return req.get(`/advancement/quiz/questions/${bookId}`) },
  submitQuizAnswers(quizId, answers) { return req.post(`/advancement/quiz/${quizId}/submit`, answers) },

  // 成就
  getAchievements() { return req.get('/advancement/achievements') },
  getChildAchievements(childId) { return req.get(`/advancement/achievements/${childId}`) },

  // 证书
  getChildCertificates(childId) { return req.get(`/certificate/${childId}`) },

  // 名片
  getProfile(childId) { return req.get(`/profile/${childId}`) },

  // 观察期报告
  getObservationReport(childId) { return req.get(`/report/observation/${childId}`) },
  getObservationReportDetail(childId) { return req.get(`/report/observation/${childId}/detail`) },
  markReportViewed(reportId) { return req.put(`/report/observation/${reportId}/viewed`) },
  getLearningReport(childId) { return req.get(`/report/learning/${childId}`) },

  // 订单
  createOrder(childId, type) { return req.post('/order/', { child_id: childId, type }) },
  cancelOrder(orderId) { return req.post(`/order/${orderId}/cancel`) },
  getOrder(id) { return req.get(`/order/${id}`) },
  getPayParams(orderId) { return req.get(`/order/${orderId}/pay-params`) },
  getOrders(page) { return req.get('/order/', null, { params: { page: page || 1 } }) },

  // 退款
  applyRefund(orderId, usedDays, reason) { return req.post('/refund/', { order_id: orderId, used_days: usedDays, reason }) },
  getRefundPreview(orderId, usedDays) { return req.get(`/order/${orderId}/refund-preview`, null, { params: { used_days: usedDays } }) },

  // 权益转让
  transferBenefit(sourceChildId, targetChildId) { return req.post('/child/transfer', { source_child_id: sourceChildId, target_child_id: targetChildId }) },

  // 场馆
  getVenues() { return req.get('/venue/list', null, { auth: false }) },

  // 活动
  getActivities(childId) { return req.get('/activity/', null, { params: { child_id: childId } }) },
  getActivity(id, childId) { return req.get(`/activity/${id}`, null, { params: { child_id: childId } }) },
  enrollActivity(activityId, childId) { return req.post('/activity/enroll', { activity_id: activityId, child_id: childId }) },
  cancelEnrollment(enrollmentId) { return req.put(`/activity/enroll/${enrollmentId}/cancel`) },
  signIn(enrollmentId) { return req.put(`/activity/enroll/${enrollmentId}/sign-in`) },

  // 押金
  getDepositStatus(childId) { return req.get('/deposit/status', null, { params: { child_id: childId } }) },
  payDeposit(childId) { return req.post('/deposit/pay', { child_id: childId }) },
  refundDeposit(childId) { return req.post('/deposit/refund', { child_id: childId }) },
  repayDeposit(childId) { return req.post('/deposit/repay', null, { params: { child_id: childId } }) },
  payFines(childId) { return req.post('/deposit/pay-fines', { child_id: childId }) },

  // 用户/孩子信息更新
  updateUserInfo(data) { return req.put('/user/info', data) },
  updateChild(childId, data) { return req.put(`/child/${childId}`, data) },

  // 消息
  getMessages(msgType, page) { return req.get('/message/', null, { params: { msg_type: msgType, page: page || 1 } }) },
  markMessageRead(messageId) { return req.put(`/message/${messageId}/read`) },
  markAllMessagesRead() { return req.put('/message/read-all') },

  // 预约
  createReservation(childId, bookId) { return req.post('/reservation/', { child_id: childId, book_id: bookId }) },
  getReservations(childId) { return req.get(`/reservation/${childId}`) },
  cancelReservation(reservationId) { return req.post(`/reservation/${reservationId}/cancel`) },
  // 转让记录
  getTransferRecords() { return req.get('/child/transfer/records') },
  deleteChild(childId) { return req.del(`/child/${childId}`) },
}
