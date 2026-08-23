// pages/member/member.js — 我的（WM6：家长信息 + 孩子切换 + 入口 + 退出）
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
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 })
    }
    this.refresh()
  },

  refresh() {
    const parent = session.getParent()
    const children = session.getChildren().map((c) => ({
      ...c,
      statusText: MEMBER_STATUS_TEXT[c.member_status] || c.member_status,
    }))
    const currentChild = session.getCurrentChild()
    const expireLine = this._expireLine(currentChild)
    this.setData({
      parent,
      children,
      currentChild,
      statusText: currentChild ? (MEMBER_STATUS_TEXT[currentChild.member_status] || currentChild.member_status) : '',
      expireLine,
      expireWarn: expireLine.indexOf('即将到期') > -1,
    })
  },

  _expireLine(c) {
    if (!c) return ''
    const fmt = (d) => {
      const dt = new Date(d)
      const m = String(dt.getMonth() + 1).padStart(2, '0')
      const day = String(dt.getDate()).padStart(2, '0')
      return `${dt.getFullYear()}-${m}-${day}`
    }
    if (c.member_status === 'none' || c.member_status === 'withdrawn') return ''
    if (c.member_status === 'expired') return '已过期（请联系店内处理）'
    const today = new Date()
    if (c.member_status === 'observation' && !c.member_expire) {
      const d = new Date(today.getTime() + 30 * 24 * 3600 * 1000)
      return `会员到期(观察期 30 天) ${fmt(d)}`
    }
    if (!c.member_expire) return ''
    const diff = Math.ceil((new Date(c.member_expire).getTime() - today.getTime()) / 86400000)
    if (c.member_status === 'formal' && diff >= 0 && diff <= 7) {
      return `会员到期 ${fmt(c.member_expire)} · 即将到期，请联系馆员续费`
    }
    return `会员到期 ${fmt(c.member_expire)}`
  },

  onSwitchChild(e) {
    session.setCurrentChild(e.currentTarget.dataset.id)
    this.refresh()
  },

  goCheckin() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/checkin/checkin?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  goRefund() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/order-pkg/refund-apply/refund-apply?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goTransfer() {
    wx.navigateTo({ url: '/pages/order-pkg/benefit-transfer/benefit-transfer' })
  },
  goVocabulary() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/reading-pkg/vocabulary/vocabulary?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goObservation() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/observation-report/observation-report?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
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

  goReport() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/report/report?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  goDeposit() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/order-pkg/deposit/deposit?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}&member_status=${c.member_status || ''}` })
  },

  goReservation() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/order-pkg/reservation/reservation?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出当前账号吗？',
      success: (res) => {
        if (!res.confirm) return
        session.logout()
        wx.reLaunch({ url: '/pages/login/login' })
      },
    })
  },
})
