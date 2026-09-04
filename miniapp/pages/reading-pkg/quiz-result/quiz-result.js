// pages/reading-pkg/quiz-result/quiz-result.js — 测验结果（WM7）
const api = require('../../../utils/api')

Page({
  data: {
    result: null,
    childId: null,
    childName: '',
    percent: 0,
    showWrong: false,
  },

  async onLoad(options) {
    const childId = Number(options.child_id)
    const bookId = options.book_id ? Number(options.book_id) : null
    let result = null
    // R9（插修9）：per-book 缓存键（quiz.js 提交时写入）——绝不再读旧单键
    // （跨书污染实锤：点 A 书金卡显示 B 书成绩）
    try { result = JSON.parse(wx.getStorageSync(`quiz_result_${childId}:${bookId}`) || 'null') } catch (e) { /* ignore */ }
    if (!result && bookId) {
      // 无缓存兜底（换设备/清缓存）：getQuiz 服务端数据构造只读成绩单——
      // 显示该书历史最佳，不带本次作答明细；拿不到才回退"没有测验记录"
      try {
        const q = await api.getQuiz(bookId, childId)
        result = {
          from_server: true,
          passed: q.status === 'passed',
          book_id: bookId,
          book_title: decodeURIComponent(options.book_title || '') || q.book_title,
          score: q.best_score || 0,
          total: (q.questions && q.questions.length) || 5,
          attempts_left: q.attempts_left || 0,
          best_score: q.best_score || 0,
          words_added: 0,
          points_detail: [],
          wrong: [],
        }
      } catch (e) { /* 落"没有测验记录"分支 */ }
    }
    if (!result) {
      wx.showToast({ title: '没有测验记录', icon: 'none' })
      setTimeout(() => wx.navigateBack(), 800)
      return
    }
    this.setData({
      result,
      childId,
      childName: decodeURIComponent(options.child_name || ''),
      // F-L6/T34：除零守卫
      percent: result.total ? Math.round((result.score * 100) / result.total) : 0,
    })
  },

  toggleWrong() { this.setData({ showWrong: !this.data.showWrong }) },

  onRetry() {
    wx.redirectTo({
      url: `/pages/reading-pkg/quiz/quiz?book_id=${this.data.result.book_id}&book_title=${encodeURIComponent(this.data.result.book_title)}&child_id=${this.data.childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },

  onBack() { wx.navigateBack() },
})
