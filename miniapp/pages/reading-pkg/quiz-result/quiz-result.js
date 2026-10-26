// pages/reading-pkg/quiz-result/quiz-result.js — 测验结果（WM7）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

Page({
  data: {
    result: null,
    childId: null,
    childName: '',
    percent: 0,
    showWrong: false,
    // WM14-A：本次提交可晒且未晒的成就卡（null=不显示「晒成就」按钮）
    shareTarget: null,
    // W7-05：取不到成绩单（网络/5xx）时的可见失败态
    loadError: '',
  },

  async onLoad(options) {
    this._options = options
    const childId = session.resolveChildId(options)
    const bookId = options.book_id ? Number(options.book_id) : null
    let result = null
    let noRecordMsg = ''
    // R9（插修9）：per-book 缓存键（quiz.js 提交时写入）——绝不再读旧单键
    // （跨书污染实锤：点 A 书金卡显示 B 书成绩）
    try { result = JSON.parse(wx.getStorageSync(`quiz_result_${childId}:${bookId}`) || 'null') } catch (e) {
      // 降级：本机缓存不可读（写坏/被清）→ 走下面的服务端兜底，不是"没有记录"
    }
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
          // P2-51：分母取**该次作答**的题量（服务端 best_total）；缺失才退回当前题量
          total: q.best_total || (q.questions && q.questions.length) || 5,
          attempts_left: q.attempts_left || 0,
          best_score: q.best_score || 0,
          percent: typeof q.best_percent === 'number' ? q.best_percent : null,
          words_added: q.words_added || 0,
          points_detail: q.points_added ? [{ points: q.points_added }] : [],
          wrong: [],
        }
      } catch (e) {
        // W7-05（第十轮 P2，2026-10-24）：**区分"确实没有记录"与"取不到"**。
        // 原先所有异常一律落 `!result` → toast「没有测验记录」+ 800ms 自动返回上一页：
        // 网络/5xx 故障被说成"孩子没有测验记录"（账上其实有成绩），且自动返回让人无法重试。
        // 只有 422/404（后端明确说"没有"）才算真无记录；0（网络）/5xx → 失败态 + 重试。
        const code = (e && e.statusCode) || 0
        // R11-d2/M9：422 也可能是"未入会/书下架"等守卫，不能一律说成"没有测验记录"；
        // 只有 404 或文案明确"没有记录"才走真无记录分支。
        const msg = (e && e.message) || ''
        noRecordMsg = msg
        const trulyMissing = code === 404 || (code === 422 && /没有|暂无|未.*记录/.test(msg))
        if (!trulyMissing) {
          this.setData({
            loadError: (e && e.message) || '挑战结果加载失败',
            childId,
            childName: decodeURIComponent(options.child_name || ''),
          })
          return
        }
      }
    }
    if (!result) {
      wx.showToast({ title: noRecordMsg || '没有挑战记录', icon: 'none' })
      setTimeout(() => wx.navigateBack(), 800)
      return
    }
    this.setData({
      result,
      childId,
      childName: decodeURIComponent(options.child_name || ''),
      // P2-51：正确率优先用服务端算好的 best_percent（与"最佳成绩"同一口径）；
      // 兜底自算时也已被 best_total 修正（不再出现 >100%）
      percent: typeof result.percent === 'number'
        ? result.percent
        : (result.total ? Math.round((result.score * 100) / result.total) : 0),
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
    // P2-50：满分卡的 ref_id 是"测验提交 id"（本页拿不到），原先只按 ref_id 取最新一张
    // → 会晒出**别的书**的满分卡。现在后端下发 book_id，按**本书**精确匹配（唯一判据）。
    const perfect = this._isPerfect()
      ? avail
          .filter((c) => c.card_type === 'perfect_quiz' && c.book_id === bookId)
          .sort((a, b) => b.ref_id - a.ref_id)[0]
      : null
    const shareTarget = perfect || finish || null
    if (shareTarget) this.setData({ shareTarget })
  },

  toggleWrong() { this.setData({ showWrong: !this.data.showWrong }) },

  // W7-05：失败态里的重试（重跑本页加载，不自动返回）
  onRetryLoad() {
    this.setData({ loadError: '' })
    return this.onLoad(this._options || {})
  },

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
