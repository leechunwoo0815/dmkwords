// pages/activity-pkg/activity-detail/activity-detail.js — 活动详情与报名（WM9）
const api = require('../../../utils/api')
const media = require('../../../utils/media')

const STATUS_TEXT = {
  enrolled: '已报名', checked_in: '已签到', pending_payment: '待收款确认',
  refund_pending: '退款待审', refunded: '已退款', cancelled: '已取消',
}

Page({
  data: {
    activity: null,
    childName: '',
    loading: true,
    loadError: false,
    enrolling: false,
    // 报名状态条（2026-09-20：详情页不再出现二维码，只给状态 + 去「我的入场券」的入口）
    stripTitle: '',
    stripSub: '',
    canShowTicket: false,
    // 图文详情块（2026-09-20 客户需求「像公众号一样」）：paragraph / image 两种
    detailBlocks: [],
  },

  onLoad(options) {
    this._activityId = Number(options.id)
    this._childId = Number(options.child_id)
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this._loadedOnce = false
    this.load()
  },

  onShow() {
    // 体验必修①：报名/退款/支付确认后回到本页必须看到最新名额与报名态；
    // 首次 onShow（onLoad 后立即触发的那次）不重复拉取。
    // **静默刷新**（2026-09-20 用户报「看图预览返回跳回页面顶部」）：不置 loading，
    // 避免整页卸载重挂丢滚动位置。
    if (this._loadedOnce && this._activityId) this.load({ silent: true })
    this._loadedOnce = true
  },

  // 2026-09-20：亮度管理随入场券一起迁到「我的入场券」页（本页不再出示码，也就没有调亮的理由）

  // silent=true：**不显示骨架屏**，直接在原页面上换数据。
  // 为什么必须这样（用户 2026-09-20 实测报障）：看图预览返回会触发 onShow → 重载；
  // 若此时把 loading 置 true，`<loading-skeleton>` 与正文是互斥的 wx:if，
  // 整页会被卸载再重挂 → **滚动位置被清空，页面跳回活动最上面**。
  // 规则：骨架屏只在"首次进入 / 出错重试"出现，回页刷新一律静默。
  async load({ silent = false } = {}) {
    if (!silent) this.setData({ loading: true })
    this.setData({ loadError: false })
    try {
      const a = await api.activityDetail(this._activityId, this._childId)
      // S4 双保险：终态记录（refunded/cancelled）不占报名入口位——后端 map 已仅回
      // 活跃态，此处防御对齐（终态置 null → "立即报名"入口恢复）
      const ACTIVE = ['pending_payment', 'enrolled', 'checked_in', 'refund_pending']
      if (a.my_enrollment && ACTIVE.indexOf(a.my_enrollment.status) === -1) {
        a.my_enrollment = null
      }
      // RB（插修 17）：hero 封面拼 token（书封面正解同款——断链第三处收口）
      a.cover_url = a.cover_url ? media.fullUrl(a.cover_url, true) : ''
      // 时间去掉秒级精度（2026-09-16 01:38:32 → 2026-09-16 01:38）
      if (a.start_at) a.start_at = String(a.start_at).replace('T', ' ').slice(0, 16)
      this.setData({
        activity: a,
        ...this._stripOf(a.my_enrollment, a.is_past),
        detailBlocks: this._blocksOf(a),
      })
    } catch (e) {
      // F-M11 族：加载失败必须有错误态+重试，不许整页空白
      this.setData({ loadError: true })
    }
    finally { this.setData({ loading: false }) }
  },

  onRetryLoad() { this.load() },

  // 报名状态条：详情页只讲"了解与报名"，出示签到码在独立页（客户 2026-09-20 口径）
  _stripOf(mine, isPast) {
    if (!mine) return { stripTitle: '', stripSub: '', canShowTicket: false }
    // 文案只说"什么时候做什么"，不指路到用户看不到的入口（用户 2026-09-20 反馈：
    // 原先写"签到码在「我的入场券」里"——那是页面名，前端没有这个入口，而按钮就在右边，
    // 属于"脱裤子放屁还找不到裤子"）
    const MAP = {
      enrolled: ['已报名', '活动当天点右侧「出示签到码」给馆员扫'],
      pending_payment: ['待收款确认', '名额已保留；馆员确认收款后即可出示签到码'],
      checked_in: ['已签到', '欢迎参加，祝阅读愉快'],
      refund_pending: ['退款审核中', '审核通过后名额释放'],
    }
    const hit = MAP[mine.status] || ['报名状态未同步', '请下拉刷新或联系馆员']
    // 只有"已报名未签到"才需要出示码；已签到/待收款/退款中都不给入口
    // 往期活动不给出示入口（活动已过，场馆不再扫码）
    return {
      stripTitle: hit[0],
      stripSub: hit[1],
      canShowTicket: !isPast && mine.status === 'enrolled',
    }
  },

  // 图文块（后端已把图片块转成带 token 的 URL；这里只做防御性过滤 + 收集预览图列表）
  _blocksOf(a) {
    const raw = (a && a.detail_blocks) || []
    const out = []
    raw.forEach((b) => {
      if (!b || !b.type) return
      if (b.type === 'paragraph' && b.text) out.push({ type: 'paragraph', text: b.text })
      else if (b.type === 'image' && b.image_url) {
        // ⚠️ 必须走 media.fullUrl：后端给的是**相对路径**，小程序 <image> 需要绝对 URL + token
        // （封面同一处理；漏这一步就是"图全都不显示"——图文上线首日实测踩到）
        out.push({
          type: 'image',
          image_url: media.fullUrl(b.image_url, true),
          caption: b.caption || '',
        })
      }
    })
    this._previewUrls = out.filter((b) => b.type === 'image').map((b) => b.image_url)
    return out
  },

  // 点图全屏预览（可左右滑动看完整组图——家长最常做的动作）
  onPreviewImage(e) {
    const url = e.currentTarget.dataset.src
    if (!url) return
    wx.previewImage({ current: url, urls: this._previewUrls || [url] })
  },

  goTicket() {
    const mine = this.data.activity && this.data.activity.my_enrollment
    if (!mine) return
    wx.navigateTo({
      url: `/pages/activity-pkg/ticket/ticket?enrollment_id=${mine.id}&child_id=${this._childId}`,
    })
  },

  async onEnroll() {
    const { activity, enrolling } = this.data
    if (enrolling) return
    const mine = activity.my_enrollment
    if (mine && ['enrolled', 'pending_payment', 'checked_in', 'refund_pending'].indexOf(mine.status) !== -1) {
      wx.showToast({ title: '已报名过此活动', icon: 'none' })
      return
    }
    this.setData({ enrolling: true })
    try {
      const r = await api.enrollActivity(this._activityId, this._childId)
      if (r.order_id) {
        wx.showModal({
          title: '报名成功（待收款）',
          content: `名额已保留，请到店支付 ${this.data.activity.fee_display} 完成报名（馆员确认收款后入场券生效）。`,
          showCancel: false,
        })
      } else {
        wx.showModal({
          title: '报名成功',
          content: '报名成功！活动当天在活动页「我的报名」里点「出示签到码」，出示给馆员扫码即可。',
          showCancel: false,
        })
      }
      this.load({ silent: true })
    } catch (e) { /* toast 已弹（已满/仅会员/重复报名等） */ }
    finally { this.setData({ enrolling: false }) }
  },

  async onCancel() {
    const mine = this.data.activity.my_enrollment
    const res = await wx.showModal({
      title: '取消报名', content: '确定取消本次报名？（名额立即释放）',
    })
    if (!res.confirm) return
    try {
      await api.cancelEnrollment(mine.id, this._childId)
      wx.showToast({ title: '已取消', icon: 'success' })
      this.load({ silent: true })
    } catch (e) { /* toast 已弹 */ }
  },

  async onRefund() {
    const mine = this.data.activity.my_enrollment
    // F-M16 挂账：退款时限文案本应从配置下发（后端键 activity_refund_cutoff_hours
    // 已存在且服务端已接线），但小程序侧无公开配置端点——本任不动后端，
    // 此处仅保留默认值常量并标注，待 /api/miniapp/configs 端点落地后接线。
    const CUTOFF_HOURS_DEFAULT = 2
    const res = await wx.showModal({
      title: '申请退款',
      content: `未签到且距开始超过 ${CUTOFF_HOURS_DEFAULT} 小时可申请全额退款（管理员审核后到账）。`,
      confirmText: '申请退款',
    })
    if (!res.confirm) return
    try {
      await api.refundApplyEnrollment(mine.id, this._childId)
      wx.showToast({ title: '已提交，等待审核', icon: 'none' })
      this.load({ silent: true })
    } catch (e) { /* toast 已弹（已签到/临期/已开始等） */ }
  },

})
