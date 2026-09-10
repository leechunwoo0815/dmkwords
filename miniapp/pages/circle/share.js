// pages/circle/share.js — 晒成就（WM14-A：可晒成就列表 → 选卡确认 → 落帖）
const api = require('../../utils/api')
const media = require('../../utils/media')

Page({
  data: {
    childId: null,
    childName: '',
    tab: 'available', // available=可晒 / shared=已晒
    available: [],
    shared: [],
    loading: true,
    sharing: false,
    // 事件触发点定位（Q5：quiz-result「晒成就」跳来时把该卡置顶）
    focusType: '',
    focusRef: null,
    // 拆分享成功后的卡片展示
    sharedPost: null,
  },

  onLoad(options) {
    this.setData({
      childId: Number(options.child_id),
      childName: decodeURIComponent(options.child_name || ''),
      // Q5：事件触发点带卡定位（quiz-result「晒成就」→ 该卡置顶便于直接晒）
      focusType: options.focus_type || '',
      focusRef: options.focus_ref ? Number(options.focus_ref) : null,
    })
  },

  onShow() {
    if (this.data.childId) this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const res = await api.circleMyCards(this.data.childId)
      this.setData({
        available: this._focusFirst(res.available || []),
        shared: res.shared || [],
      })
    } catch (e) { /* request.js 已 toast */ }
    finally { this.setData({ loading: false }) }
  },

  // 定位：把事件触发点指定的卡排到可晒列表首位（找不到则保持原序）
  _focusFirst(cards) {
    const { focusType, focusRef } = this.data
    if (!focusType) return cards
    const idx = cards.findIndex((c) => c.card_type === focusType && c.ref_id === focusRef)
    if (idx <= 0) return cards
    const hit = cards[idx]
    return [hit, ...cards.slice(0, idx), ...cards.slice(idx + 1)]
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
  },

  // 选卡确认分享（家长主动授权晒——系统绝不自动发帖）
  onShareCard(e) {
    const card = e.currentTarget.dataset.card
    if (!card || this.data.sharing) return
    wx.showModal({
      title: '晒这张成就卡？',
      content: `「${card.title}」将分享到馆内阅读圈，仅家长可删除。`,
      confirmText: '晒出去',
      cancelText: '再想想',
      success: (r) => {
        if (r.confirm) this.doShare(card)
      },
    })
  },

  async doShare(card) {
    this.setData({ sharing: true })
    try {
      const res = await api.circleShare(this.data.childId, card.card_type, card.ref_id)
      // 晒卡成功：展示生成的成就卡片（可保存转发）
      this.setData({ sharedPost: { ...res, image_url: media.fullUrl(res.image_url, true) } })
    } catch (e) { /* request.js 已 toast（422 含日限/已晒过原因） */ }
    finally { this.setData({ sharing: false }) }
  },

  // 成功卡片全屏预览（长按保存）
  onPreviewShared() {
    if (!this.data.sharedPost || !this.data.sharedPost.image_url) return
    wx.previewImage({ urls: [this.data.sharedPost.image_url] })
  },

  // 继续晒（回到列表）
  onKeepSharing() {
    this.setData({ sharedPost: null })
    this.load()
  },

  // 去阅读圈看效果
  goCircle() {
    wx.switchTab({ url: '/pages/circle/circle' })
  },
})
