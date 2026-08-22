// pages/index/index.js — 首页：孩子切换 + 今日打卡 + 快捷入口
const api = require('../../utils/api')
const session = require('../../utils/session')

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
    totalFinished: 0,
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
    this.setData({ parent, children, currentChild, statusText: currentChild ? (MEMBER_STATUS_TEXT[currentChild.member_status] || currentChild.member_status) : '' })
    if (currentChild) this.loadCheckin(currentChild.id)
  },

  async loadCheckin(childId) {
    try {
      const res = await api.getCheckins(childId, 60)
      this.setData({ todayChecked: !!res.today_checked, currentStreak: res.current_streak || 0 })
    } catch (e) { /* 静默 */ }
  },

  onSwitchChild(e) {
    const id = e.currentTarget.dataset.id
    session.setCurrentChild(id)
    this.refresh()
  },

  goBooks() { wx.switchTab({ url: '/pages/books/books' }) },
  goCheckin() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/checkin/checkin?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
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
})
