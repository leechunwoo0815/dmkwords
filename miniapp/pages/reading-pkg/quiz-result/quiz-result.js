// pages/reading-pkg/quiz-result/quiz-result.js — 测验结果（WM7）
Page({
  data: {
    result: null,
    childId: null,
    childName: '',
    percent: 0,
    showWrong: false,
  },

  onLoad(options) {
    const childId = Number(options.child_id)
    let result = null
    try { result = JSON.parse(wx.getStorageSync(`quiz_result_${childId}`) || 'null') } catch (e) { /* ignore */ }
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
