// pages/reading-pkg/quiz/quiz.js — 线上测验（WM7：3 次终身机会；未提交退出不占次）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F']

Page({
  data: {
    bookId: null,
    bookTitle: '',
    childId: null,
    childName: '',
    status: '',           // available / passed / failed / locked
    attemptsLeft: 0,
    bestScore: 0,
    questions: [],
    current: 0,
    answers: [],
    selected: null,
    showConfirm: false,
    submitting: false,
  },

  onLoad(options) {
    this.setData({
      bookId: Number(options.book_id),
      bookTitle: decodeURIComponent(options.book_title || ''),
      childId: Number(options.child_id),
      childName: decodeURIComponent(options.child_name || ''),
    })
    this.load()
  },

  async load() {
    const { bookId, childId } = this.data
    try {
      const q = await api.getQuiz(bookId, childId)
      this.setData({
        status: q.status, attemptsLeft: q.attempts_left, bestScore: q.best_score,
        questions: q.questions || [], answers: [], current: 0, selected: null,
      })
    } catch (e) { /* request.js 已 toast */ }
  },

  onPick(e) {
    const idx = e.currentTarget.dataset.index
    this.setData({ selected: idx })
    const answers = this.data.answers
    answers[this.data.current] = idx
    this.setData({ answers })
  },

  onNext() {
    if (this.data.selected === null) {
      wx.showToast({ title: '先选一个答案', icon: 'none' }); return
    }
    if (this.data.current < this.data.questions.length - 1) {
      this.setData({ current: this.data.current + 1, selected: this.data.answers[this.data.current + 1] ?? null })
    } else {
      this.setData({ showConfirm: true })
    }
  },

  onPrev() {
    if (this.data.current > 0) {
      this.setData({ current: this.data.current - 1, selected: this.data.answers[this.data.current - 1] ?? null })
    }
  },

  onCancelSubmit() { this.setData({ showConfirm: false }) },

  async onConfirmSubmit() {
    const { bookId, childId, questions, answers } = this.data
    if (answers.filter((a) => a !== null && a !== undefined).length < questions.length) {
      wx.showToast({ title: '还有题没答完', icon: 'none' })
      this.setData({ showConfirm: false })
      return
    }
    this.setData({ submitting: true, showConfirm: false })
    // 答案转文本：单选取选项内容，判断取 对/错
    const payload = questions.map((q, i) => q.options[answers[i]])
    try {
      const result = await api.submitQuiz(bookId, childId, payload)
      wx.setStorageSync(`quiz_result_${childId}`, JSON.stringify({
        ...result, book_title: this.data.bookTitle,
        total: questions.length, attempts_left: result.attempts_left,
      }))
      wx.redirectTo({
        url: `/pages/reading-pkg/quiz-result/quiz-result?child_id=${childId}&child_name=${encodeURIComponent(this.data.childName)}`,
      })
    } catch (e) { /* toast 已弹 */ } finally {
      this.setData({ submitting: false })
    }
  },
})
