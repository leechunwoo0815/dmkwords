// pages/circle/share.js — 晒成就（WM14-A：可晒成就列表 → 选卡确认 → 落帖）
const api = require('../../utils/api')
const media = require('../../utils/media')
const img = require('../../utils/img')
const session = require('../../utils/session')

Page({
  // P3（第一百一十六批）：晒出的卡片图加载失败兜底（清空该字段 → 走同页失败占位分支）
  onImgError(e) { img.onImgError.call(this, e) },
  data: {
    childId: null,
    childName: '',
    tab: 'available', // available=可晒 / shared=已晒
    available: [],
    shared: [],
    loading: true,
    sharing: false,
    // W7-06（第十轮 P2，2026-10-24）：① 取数失败原先静默 catch → 渲染成「还没有可晒的新成就」
    // 且两个 tab 显示 (0)(0)（把故障说成"没有成就"）；② 无 child_id 时 `Number(undefined)=NaN`
    // → onShow 的 `if (this.data.childId)` 为假 → 永不 load → 永久骨架。两者都补可见态。
    loadError: '',
    // 事件触发点定位（Q5：quiz-result「晒成就」跳来时把该卡置顶）
    focusType: '',
    focusRef: null,
    // 拆分享成功后的卡片展示
    sharedPost: null,
  },

  onLoad(options) {
    this.setData({
      // W7-06：无参不再落 NaN（那会让页面永久停在骨架屏）——统一解析 + 走可见态
      childId: session.resolveChildId(options),
      // W7-12：缺参回落到当前孩子
      childName: decodeURIComponent(options.child_name || '') || ((session.getCurrentChild() || {}).name || ''),
      // Q5：事件触发点带卡定位（quiz-result「晒成就」→ 该卡置顶便于直接晒）
      focusType: options.focus_type || '',
      focusRef: options.focus_ref ? Number(options.focus_ref) : null,
    })
  },

  onShow() {
    if (!this.data.childId) {
      this.setData({ loading: false, loadError: '没有选择孩子（请先在「我的」里添加或选择孩子）' })
      return
    }
    this.load()
  },

  onRetryLoad() { this.load() },

  async load() {
    this.setData({ loading: true, loadError: '' })
    try {
      const res = await api.circleMyCards(this.data.childId)
      this.setData({
        available: this._focusFirst(res.available || []),
        shared: res.shared || [],
      })
    } catch (e) {
      // W7-06：失败必须落到可见失败态（"还没有可晒的新成就"是误导）
      this.setData({ available: [], shared: [], loadError: (e && e.message) || '成就卡加载失败' })
    }
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
