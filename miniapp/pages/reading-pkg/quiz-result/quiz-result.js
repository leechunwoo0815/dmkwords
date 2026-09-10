// pages/reading-pkg/quiz-result/quiz-result.js — 测验结果（WM7）
const api = require('../../../utils/api')

Page({
  data: {
    result: null,
    childId: null,
    childName: '',
    percent: 0,
    showWrong: false,
    // WM14-A：本次提交可晒且未晒的成就卡（null=不显示「晒成就」按钮）
    shareTarget: null,
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
        // 插修10：words_added/points_added 用服务端真实到账（护照片同源）——
        // 原硬编码 0 与实际入账矛盾（用户目视实锤）
        result = {
          from_server: true,
          passed: q.status === 'passed',
          book_id: bookId,
          book_title: decodeURIComponent(options.book_title || '') || q.book_title,
          score: q.best_score || 0,
          total: (q.questions && q.questions.length) || 5,
          attempts_left: q.attempts_left || 0,
          best_score: q.best_score || 0,
          words_added: q.words_added || 0,
          points_detail: q.points_added ? [{ points: q.points_added }] : [],
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
    // WM14-A（Q5 裁决）：事件触发点接线——本次提交若产生了"可晒且未晒"的成就
    // 才显示「晒成就」按钮（满分优先满分卡，否则完读卡）；拉取失败则静默不显示
    if (result.passed) this._loadShareTarget()
  },

  _isPerfect() {
    const r = this.data.result
    return !!(r && r.total && r.score === r.total)
  },

  async _loadShareTarget() {
    const childId = this.data.childId
    const bookId = this.data.result.book_id
    let res
    try {
      res = await api.circleMyCards(childId)
    } catch (e) {
      return // 拿不到可晒库就不显示按钮（不阻塞成绩单阅读）
    }
    const avail = (res && res.available) || []
    // 完读卡 ref_id=book_id（Q10 口径，精确匹配）
    const finish = avail.find((c) => c.card_type === 'finish_book' && c.ref_id === bookId)
    // 满分卡 ref_id=测验提交 id（本页拿不到提交 id）——取该书最新一张未晒满分卡
    const perfect = this._isPerfect()
      ? avail
          .filter((c) => c.card_type === 'perfect_quiz')
          .sort((a, b) => b.ref_id - a.ref_id)[0]
      : null
    const shareTarget = perfect || finish || null
    if (shareTarget) this.setData({ shareTarget })
  },

  toggleWrong() { this.setData({ showWrong: !this.data.showWrong }) },

  // WM14-A（Q5 裁决）：结果页「晒成就」——跳晒卡页并定位到本次可晒的成就卡
  onShare() {
    const t = this.data.shareTarget
    if (!t) return
    wx.navigateTo({
      url:
        `/pages/circle/share?child_id=${this.data.childId}` +
        `&child_name=${encodeURIComponent(this.data.childName || '')}` +
        `&focus_type=${t.card_type}&focus_ref=${t.ref_id}`,
    })
  },

  onRetry() {
    wx.redirectTo({
      url: `/pages/reading-pkg/quiz/quiz?book_id=${this.data.result.book_id}&book_title=${encodeURIComponent(this.data.result.book_title)}&child_id=${this.data.childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },

  onBack() { wx.navigateBack() },
})
