// pages/activity-pkg/activity-detail/activity-detail.js — 活动详情与报名（WM9）
const api = require('../../../utils/api')
const media = require('../../../utils/media')
const ticketCode = require('../../../utils/ticket-code')

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
    // 入场券码图片（canvasToTempFilePath 产物）；空 = 出图失败，露出券码文本兜底
    qrImage: '',
    bcImage: '',
    // 已签到时间（HH:MM）与"已调亮屏幕"提示
    checkedInTime: '',
    brightnessBoosted: false,
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
    // 首次 onShow（onLoad 后立即触发的那次）不重复拉取
    if (this._loadedOnce && this._activityId) this.load()
    this._loadedOnce = true
  },

  // 离开页面必须把亮度还回去——改系统亮度是全局副作用，不还原就是骚扰用户
  onHide() { this._restoreBrightness() },
  onUnload() {
    this._clearDrawTimers()
    this._restoreBrightness()
  },

  async load() {
    this.setData({ loading: true, loadError: false })
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
      const mine = a.my_enrollment
      this.setData({
        activity: a,
        checkedInTime: mine && mine.checked_in_at
          ? String(mine.checked_in_at).replace('T', ' ').slice(11, 16) : '',
      }, () => {
        this._scheduleTicketDraw(a)
        // 有券才调亮：没券的页面调亮只会让人莫名其妙
        if (mine) this._boostBrightness()
      })
    } catch (e) {
      // F-M11 族：加载失败必须有错误态+重试，不许整页空白
      this.setData({ loadError: true })
    }
    finally { this.setData({ loading: false }) }
  },

  onRetryLoad() { this.load() },

  // 入场券出图调度：画布是"隐藏的渲染后端"，出图是异步的，故画完再导出成图片。
  // 首次若在布局未稳时查询节点，尺寸可能拿不到 → 补一次；出图本身与布局无关，不存在错位。
  _scheduleTicketDraw(a) {
    if (!a || !a.my_enrollment || !a.my_enrollment.ticket_code) return
    this._clearDrawTimers()
    this._drawTimers = [0, 300].map((ms) => setTimeout(() => this._renderTicket(a), ms))
  },

  _clearDrawTimers() {
    if (this._drawTimers) {
      this._drawTimers.forEach(clearTimeout)
      this._drawTimers = []
    }
  },

  // 展示入场券时把屏幕亮度拉满（扫码枪成功率）+ 保持常亮（出示码时别息屏）。
  // 原亮度先存下来，onHide/onUnload 还原；拿不到原值就不设（宁可不调亮，也不能还原成黑屏）。
  // ⚠️ 开发者工具里 setScreenBrightness 是空实现/会失败，真机才有效果。
  _boostBrightness() {
    if (this._brightnessSaved !== undefined) return
    wx.setKeepScreenOn({ keepScreenOn: true, fail: () => {} })
    wx.getScreenBrightness({
      success: (res) => {
        const v = typeof res.value === 'number' ? res.value : -1
        this._brightnessSaved = v > 0 && v <= 1 ? v : null
        wx.setScreenBrightness({
          value: 1,
          success: () => { if (v > 0 && !this.data.brightnessBoosted) this.setData({ brightnessBoosted: true }) },
          fail: () => {},
        })
      },
      fail: () => { this._brightnessSaved = null },
    })
  },

  _restoreBrightness() {
    wx.setKeepScreenOn({ keepScreenOn: false, fail: () => {} })
    const saved = this._brightnessSaved
    this._brightnessSaved = undefined
    if (typeof saved === 'number' && saved > 0) {
      wx.setScreenBrightness({ value: saved, fail: () => {} })
    }
  },

  // 入场券码出图（PRD §9.2.1）：二维码（主）+ Code128 条形码（备），纯本地计算。
  // 画布只是"渲染后端"（移出可视区），**展示一律用 canvasToTempFilePath 出来的 <image>**：
  // `canvas type="2d"` 是原生层，首帧坐标早于布局稳定时会停在旧位置且不自愈
  // （实测二维码画到 hero 上、条形码画到按钮附近 = 用户报的"遮挡"），当图片显示就没有这个问题。
  _renderTicket(a) {
    const mine = a && a.my_enrollment
    if (!mine || !mine.ticket_code) return
    const code = mine.ticket_code
    const query = wx.createSelectorQuery().in(this)
    query.select('#ticket-qr').fields({ node: true, size: true })
    query.select('#ticket-bc').fields({ node: true, size: true })
    query.exec((res) => {
      const qr = res && res[0]
      const bc = res && res[1]
      const dpr = this._pixelRatio()
      // 二维码画布做成正方形：取宽高里较小的那个，保证画圆不裁边
      const qrSize = qr && qr.width ? Math.min(qr.width, qr.height) : 0
      if (qr && qr.node && qrSize) {
        const ctx = qr.node.getContext('2d')
        qr.node.width = qrSize * dpr // 设 width 会重置变换矩阵，故每次都要重设
        qr.node.height = qrSize * dpr
        ctx.scale(dpr, dpr)
        if (ticketCode.drawQr(ctx, code, qrSize, {})) {
          this._exportCanvas(qr.node, qrSize, qrSize, dpr, 'qrImage')
        }
      }
      if (bc && bc.node && bc.width) {
        const ctx = bc.node.getContext('2d')
        bc.node.width = bc.width * dpr
        bc.node.height = bc.height * dpr
        ctx.scale(dpr, dpr)
        if (ticketCode.drawBarcode(ctx, code, bc.width, bc.height, {})) {
          this._exportCanvas(bc.node, bc.width, bc.height, dpr, 'bcImage')
        }
      }
    })
  },

  // 画布 → 临时图片文件 → setData（导出失败就不显示，页面仍有券码文本兜底）
  _exportCanvas(node, w, h, dpr, key) {
    wx.canvasToTempFilePath({
      canvas: node,
      x: 0, y: 0, width: w, height: h,
      destWidth: Math.round(w * dpr), destHeight: Math.round(h * dpr),
      success: (res) => { this.setData({ [key]: res.tempFilePath }) },
      fail: () => {},
    })
  },

  _pixelRatio() {
    try {
      if (wx.getWindowInfo) return wx.getWindowInfo().pixelRatio || 2
      return wx.getSystemInfoSync().pixelRatio || 2
    } catch (e) { return 2 }
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
          content: `入场券码 ${r.enrollment.ticket_code}，活动当天出示给馆员扫码签到。`,
          showCancel: false,
        })
      }
      this.load()
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
      this.load()
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
      this.load()
    } catch (e) { /* toast 已弹（已签到/临期/已开始等） */ }
  },

  copyTicket() {
    const mine = this.data.activity && this.data.activity.my_enrollment
    if (!mine) return
    wx.setClipboardData({ data: mine.ticket_code })
  },
})
