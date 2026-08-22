// miniapp/utils/api.js — WM6 家长端 API 层（新契约 /api/miniapp/*）
const req = require('./request')

module.exports = {
  // 登录（开发期：手机号 + 验证码 1234；上线前接微信 code2session）
  login(phone, code) {
    return req.post('/api/miniapp/login', { phone, code }, { auth: false })
  },

  // 书目
  listBooks(keyword, page, pageSize) {
    return req.get('/api/miniapp/books', null, {
      params: { keyword: keyword || '', page: page || 1, page_size: pageSize || 20 },
    })
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

  // 生词本 / 收藏 / 书架（WM8）
  lookupWord(word, childId, bookId) {
    return req.get('/api/miniapp/vocabulary/lookup', null, {
      params: { word, child_id: childId, book_id: bookId || '' },
    })
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
  currentBorrows(childId) {
    return req.get('/api/miniapp/borrows', null, { params: { child_id: childId } })
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
}
