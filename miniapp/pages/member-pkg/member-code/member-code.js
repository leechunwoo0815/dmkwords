// pages/member-pkg/member-code/member-code.js — 会员码（借阅台扫码识别用，2026-09-21 任务包 B 批）
// 孩子到店 → 点首页「会员码」→ 把这页递给馆员 → 馆员的扫码枪扫二维码即完成身份识别。
// **码由后端生成**（不可枚举 + 校验位，见 backend/domain/identity/member_code.py），
// 本页只负责展示：二维码（主）+ Code128 条形码（备）+ 大字号文本码（手输兜底）——
// 前端不参与任何派生计算（用户拍板：防伪造）。
const session = require('../../../utils/session')
const ticketCode = require('../../../utils/ticket-code')

Page({
  data: {
    childId: 0,
    childName: '',
    code: '',
    qrImage: '',
    bcImage: '',
    brightnessBoosted: false,
    copied: false,
    noCode: false,
  },

  onLoad() {
    if (!session.ensureLogin()) return
    this._apply()
    // 本地 children 只是登录那一刻的快照；A 批之前登录的会话没有 member_code 字段，
    // 拉一次最新列表再读（refreshChildren 自身有 30s 节流与超时，失败静默保留旧缓存）。
    session.refreshChildren().then(() => this._apply())
  },

  onShow() {
    // 切孩子后回到本页必须重算（session 无事件机制，onShow 重读是既有口径）
    if (this._hasCode !== undefined) this._apply()
  },

  onHide() {
    this._restoreBrightness()
  },

  onUnload() {
    this._restoreBrightness()
    this._clearDrawTimers()
  },

  _apply() {
    const child = session.getCurrentChild()
    if (!child) {
      this._hasCode = false
      this.setData({ childId: 0, childName: '', code: '', qrImage: '', bcImage: '', noCode: true })
      return
    }
    const code = String(child.member_code || '').toUpperCase()
    const changed = code !== this.data.code || child.id !== this.data.childId
    this._hasCode = !!code
    this.setData({
      childId: child.id,
      childName: child.name || '',
      code,
      noCode: !code,
      copied: false,
      ...(changed ? { qrImage: '', bcImage: '' } : {}),
    })
    if (!changed || !code) return
    this._boostBrightness()
    this._scheduleDraw()
  },

  // ---------- 出图（与活动入场券页同一套画布口径） ----------
  // 画布只是"渲染后端"（`.draw-canvas*` 移出可视区），展示一律用 canvasToTempFilePath
  // 出来的 <image>：`canvas type="2d"` 是原生层，首帧坐标早于布局稳定时会停在旧位置且不自愈。
  _scheduleDraw() {
    this._clearDrawTimers()
    // 两次尝试：首次可能在布局未稳时拿不到画布尺寸（布局无关的出图不存在错位问题）
    this._drawTimers = [0, 300].map((ms) => setTimeout(() => this._render(), ms))
  },

  _clearDrawTimers() {
    if (this._drawTimers) {
      this._drawTimers.forEach(clearTimeout)
      this._drawTimers = []
    }
  },

  _render() {
    const code = this.data.code
    if (!code) return
    const query = wx.createSelectorQuery().in(this)
    query.select('#mc-qr').fields({ node: true, size: true })
    query.select('#mc-bc').fields({ node: true, size: true })
    query.exec((res) => {
      const qr = res && res[0]
      const bc = res && res[1]
      const dpr = this._pixelRatio()
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

  _exportCanvas(node, w, h, dpr, key) {
    wx.canvasToTempFilePath({
      canvas: node,
      x: 0,
      y: 0,
      width: w,
      height: h,
      destWidth: Math.round(w * dpr),
      destHeight: Math.round(h * dpr),
      success: (res) => this.setData({ [key]: res.tempFilePath }),
      fail: () => {}, // 导出失败不显示图，页面仍有文本码兜底
    })
  },

  _pixelRatio() {
    try {
      if (wx.getWindowInfo) return wx.getWindowInfo().pixelRatio || 2
      return wx.getSystemInfoSync().pixelRatio || 2
    } catch (e) {
      return 2
    }
  },

  // ---------- 出示期间的屏幕亮度（沿用 PRD §9.2.1 口径：拉满 + 常亮，离开还原） ----------
  // ⚠️ 开发者工具里 setScreenBrightness 是空实现，真机才有效果。
  _boostBrightness() {
    if (this._brightnessSaved !== undefined) return
    wx.setKeepScreenOn({ keepScreenOn: true, fail: () => {} })
    wx.getScreenBrightness({
      success: (res) => {
        const v = typeof res.value === 'number' ? res.value : -1
        // 拿不到原值就不设（宁可不调亮，也不能还原成黑屏）
        this._brightnessSaved = v > 0 && v <= 1 ? v : null
        wx.setScreenBrightness({
          value: 1,
          success: () => {
            if (v > 0 && !this.data.brightnessBoosted) this.setData({ brightnessBoosted: true })
          },
          fail: () => {},
        })
      },
      fail: () => {
        this._brightnessSaved = null
      },
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

  // 底部「关闭会员码」：回首页。本页是从首页 navigateTo 进来的，navigateBack 即可；
  // 兜底用 switchTab（首页是 tabBar 页）——直接把用户送到首页，不走左上角小箭头。
  closePage() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: '/pages/index/index' }),
    })
  },

  copyCode() {
    if (!this.data.code) return
    wx.setClipboardData({
      data: this.data.code,
      success: () => this.setData({ copied: true }),
    })
  },
})
