// pages/member/member.js — 我的（WM6：家长信息 + 孩子切换 + 入口 + 退出）
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
    // WM15-R3：内置头像库（与后端 AVATAR_IDS 同源的展示副本）+ 宫格选择弹层
    avatarOptions: [],
    showAvatarPicker: false,
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 4 })
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
    if (!this.data.avatarOptions.length) {
      // 清单为生成物（源：backend .../art.py AVATAR_IDS）——不手写，防三端漂移
      const opts = require('../../utils/avatars').AVATAR_IDS.map((id) => ({
        id,
        url: `/icons/avatars/${id}.png`,
        active: !!currentChild && currentChild.avatar === id,
      }))
      this.setData({ avatarOptions: opts })
    } else if (currentChild) {
      this.setData({
        avatarOptions: this.data.avatarOptions.map((o) => ({ ...o, active: o.id === currentChild.avatar })),
      })
    }
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
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34 ''
    const fmt = (d) => {
      const dt = new Date(String(d).replace(/-/g, '/'))
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
    const diff = Math.ceil((new Date(String(c.member_expire).replace(/-/g, '/')).getTime() - today.getTime()) / 86400000)
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
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/checkin/checkin?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  goMessages() {
    wx.navigateTo({ url: '/pages/order-pkg/messages/messages' })
  },

  goRefund() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/order-pkg/refund-apply/refund-apply?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goTransfer() {
    wx.navigateTo({ url: '/pages/order-pkg/benefit-transfer/benefit-transfer' })
  },
  goVocabulary() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/reading-pkg/vocabulary/vocabulary?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goObservation() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/observation-report/observation-report?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goLeaderboard() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/leaderboard/leaderboard?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goPassport() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/profile-card/profile-card?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goAchievement() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/achievement/achievement?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  goReport() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/member-pkg/report/report?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  goOrders() {
    const c = this.data.currentChild || {}
    wx.navigateTo({ url: `/pages/order-pkg/order-history/order-history?child_id=${c.id || ''}&child_name=${encodeURIComponent(c.name || '')}` })
  },

  goDeposit() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/order-pkg/deposit/deposit?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}&member_status=${c.member_status || ''}` })
  },

  goReservation() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return } // F-L17/T34
    wx.navigateTo({ url: `/pages/order-pkg/reservation/reservation?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },

  // WM14-B：设置展示称呼（阅读圈双署名/被赞通知取它，空=回退真实姓名）
  onEditDisplayName() {
    wx.showModal({
      title: '设置称呼',
      editable: true,
      placeholderText: '如：Tommy妈妈（最多 20 字）',
      success: async (res) => {
        if (!res.confirm) return
        const value = (res.content || '').trim()
        if (value.length > 20) {
          wx.showToast({ title: '称呼最多 20 个字', icon: 'none' })
          return
        }
        try {
          const r = await api.updateParentProfile(value)
          session.patchParent({ display_name: r.display_name || '' })
          this.refresh()
          wx.showToast({ title: '已保存', icon: 'success' })
        } catch (e) { /* request.js 已 toast */ }
      },
    })
  },

  noop() {},

  // WM15-R3：点孩子头像 → 宫格选择内置头像（24 枚：12 动物 × 2 配色）
  openAvatarPicker() {
    const c = this.data.currentChild
    if (!c) { wx.showToast({ title: '请先添加孩子档案', icon: 'none' }); return }
    this.setData({ showAvatarPicker: true })
  },

  closeAvatarPicker() {
    this.setData({ showAvatarPicker: false })
  },

  async onPickAvatar(e) {
    const id = e.currentTarget.dataset.id
    const c = this.data.currentChild
    if (!c || !id) return
    try {
      await api.updateChildAvatar(c.id, id)
      // 本地缓存同步（session 里 children 是数组，逐项替换）
      const children = session.getChildren().map((x) => (x.id === c.id ? { ...x, avatar: id } : x))
      wx.setStorageSync('children', children)
      this.setData({ showAvatarPicker: false })
      this.refresh()
      wx.showToast({ title: '头像已更新', icon: 'success' })
    } catch (err) { /* request.js 已 toast */ }
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
