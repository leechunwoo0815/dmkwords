// pages/index/index.js — 首页 v5：孩子的今日任务台（续听/打卡/数据/真推荐/快捷入口）
const api = require('../../utils/api')
const session = require('../../utils/session')
const media = require('../../utils/media')

const MEMBER_STATUS_TEXT = {
  none: '未入会', observation: '观察期', pending_evaluation: '待评估',
  formal: '正式会员', expired: '已过期', withdrawn: '已退会',
}

Page({
  data: {
    parent: null,
    children: [],
    currentChild: null,
    statusText: '',
    todayChecked: false,
    currentStreak: 0,
    // 续听卡（在借第一本 + 真实进度）
    continueBook: null,
    // 今日数据条
    totalWords: 0,
    points: 0,
    // 提醒条
    unreadCount: 0,
    borrowCount: 0,
    reservationCount: 0,
    // 今日推荐（真实书目，横滑）
    recommend: [],
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.refresh()
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 0 })
    }
  },

  refresh() {
    const parent = session.getParent()
    const children = session.getChildren().map((c) => ({
      ...c,
      statusText: MEMBER_STATUS_TEXT[c.member_status] || c.member_status,
    }))
    const currentChild = session.getCurrentChild()
    this.setData({
      parent,
      children,
      currentChild,
      statusText: currentChild ? (MEMBER_STATUS_TEXT[currentChild.member_status] || currentChild.member_status) : '',
    })
    if (currentChild) this.loadAll(currentChild)
  },

  loadAll(child) {
    this.loadCheckin(child.id)
    this.loadGrowth(child.id)
    this.loadContinue(child.id)
    this.loadBadges(child.id)
    this.loadRecommend()
  },

  async loadCheckin(childId) {
    try {
      const res = await api.getCheckins(childId, 60)
      this.setData({ todayChecked: !!res.today_checked, currentStreak: res.current_streak || 0 })
    } catch (e) { /* 静默 */ }
  },

  async loadGrowth(childId) {
    try {
      const g = await api.growthSummary(childId)
      this.setData({
        totalWords: g.words_total || 0,
        points: g.points_total || 0,
      })
    } catch (e) { /* 静默 */ }
  },

  // 续听卡：最近一本"有进度未读完"（finish=0 的最近一本；读完/无进度不显示）
  async loadContinue(childId) {
    try {
      const [borrows, cont] = await Promise.all([
        api.currentBorrows(childId).catch(() => []),
        api.continueListening(childId).catch(() => null),
      ])
      this.setData({ borrowCount: (borrows || []).length })
      if (!cont || !cont.book) {
        this.setData({ continueBook: null })
        return
      }
      this.setData({
        continueBook: {
          ...media.formatBook(cont.book),
          id: cont.book.id,
          percent: cont.percent || 0,
          lastPosition: cont.last_position || 0,
          dueText: cont.due_at ? this.dueText(cont) : '可续听',
        },
      })
    } catch (e) {
      this.setData({ continueBook: null })
    }
  },

  dueText(borrow) {
    const due = borrow.due_date || borrow.due_at
    if (!due) return ''
    const days = Math.ceil((new Date(String(due).replace(/-/g, '/')) - new Date()) / 86400000)
    if (days < 0) return `已逾期 ${-days} 天`
    if (days === 0) return '今天到期'
    return `${days} 天后到期`
  },

  // 提醒条：未读消息 + 预约中数量
  async loadBadges(childId) {
    try {
      const n = await api.notifications(1, 1)
      this.setData({ unreadCount: n.unread || 0 })
    } catch (e) { /* 静默 */ }
    try {
      const rs = await api.listReservations(childId)
      // F-M1/T26：后端枚举是 active（waiting/ready 不存在，枚举错配同族第 4 案，徽标恒 0）
      const active = (rs || []).filter((r) => r.status === 'active')
      this.setData({ reservationCount: active.length })
    } catch (e) { /* 静默 */ }
  },

  // 今日推荐：从书库取 6 本真实书目（封面横滑卡）
  async loadRecommend() {
    try {
      const res = await api.listBooks('', 1, 6)
      this.setData({ recommend: media.formatBooks(res.items || []) })
    } catch (e) { /* 静默 */ }
  },

  onSwitchChild(e) {
    const id = e.currentTarget.dataset.id
    session.setCurrentChild(id)
    this.refresh()
  },

  goContinue() {
    const b = this.data.continueBook
    const c = this.data.currentChild
    if (!b || !c) return
    wx.navigateTo({
      url: `/pages/reading-pkg/reader/reader?book=${encodeURIComponent(JSON.stringify(b))}&child_id=${c.id}`,
    })
  },
  goRecommend(e) {
    const book = e.currentTarget.dataset.book
    const c = this.data.currentChild
    if (!book) return
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book=${encodeURIComponent(JSON.stringify(book))}${c ? `&child_id=${c.id}` : ''}`,
    })
  },
  goMoreBooks() { wx.switchTab({ url: '/pages/books/books' }) },
  goMessage() { wx.navigateTo({ url: '/pages/order-pkg/messages/messages' }) },
  goShelf() { wx.switchTab({ url: '/pages/shelf/shelf' }) },
  goCheckin() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/checkin/checkin?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goAchievement() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/achievement/achievement?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goReservation() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/order-pkg/reservation/reservation?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goLeaderboard() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/leaderboard/leaderboard?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goPassport() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/profile-card/profile-card?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goActivities() {
    wx.navigateTo({ url: '/pages/activity-pkg/activity-list/activity-list' })
  },
  goVocabulary() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/reading-pkg/vocabulary/vocabulary?child_id=${c.id}` })
  },
})
