// pages/activity-pkg/ticket/ticket.js — 我的入场券（签到码出示页）
// 2026-09-20 客户口径变更：二维码从活动详情页迁到本页，家长主动点「出示签到码」才进入。
// 详情页不再出现任何码；本页承载二维码 + 条形码 + 券码 + 签到状态 + 屏幕调亮/常亮。
const api = require('../../../utils/api')
const session = require('../../../utils/session')
const ticketCode = require('../../../utils/ticket-code')

//: 可出示入场券的报名态（待收款 pending_payment 不发放——"确认收款后入场券生效"）
const TICKET_STATUSES = ['enrolled', 'checked_in']
//: 在场轮询间隔（家长举码这段时间才轮询；签到成功/离开页面即停）
const WATCH_INTERVAL_MS = 3000
//: 签到成功后停留多久再跳转（让家长看清"签到成功"）
const SUCCESS_HOLD_MS = 1400

Page({
  data: {
    loading: true,
    loadError: false,
    errorTitle: '入场券加载失败',
    errorDesc: '请检查网络后重试，或回到活动列表重新进入',
    enrollment: null,
    activityTitle: '',
    startAt: '',
    childName: '',
    checkedIn: false,
    checkedInTime: '',
    // 出图产物（canvasToTempFilePath）；空 = 出图失败，露出券码文本兜底
    qrImage: '',
    bcImage: '',
    brightnessBoosted: false,
    // 2026-09-20 用户反馈：家长举着码站在馆员面前，页面**不会自己变**（onShow 只在切回时触发）
    // → 在场轮询 + 签到成功动效 + 自动跳回活动详情
    checkinSuccess: false,
  },

  onLoad(options) {
    if (!session.ensureLogin()) return
    this._enrollmentId = Number(options.enrollment_id)
    this._childId = Number(options.child_id)
    const child = session.getCurrentChild()
    this.setData({ childName: child ? child.name : '' })
    this._loadedOnce = false
    this.load()
  },

  onShow() {
    // 馆员扫完码后家长应看到「已签到」——回到本页（含从后台切回）重新拉一次状态；
    // 首次 onShow（onLoad 后那次）不重复请求
    if (this._loadedOnce && this._enrollmentId) this.load()
    this._loadedOnce = true
    this._startWatch()
  },

  // 离开页面必须把亮度还回去——改系统亮度是全局副作用，不还原就是骚扰用户；同时停轮询
  onHide() {
    this._stopWatch()
    this._restoreBrightness()
  },
  onUnload() {
    this._stopWatch()
    this._clearDrawTimers()
    this._restoreBrightness()
  },

  // 在场轮询：家长举着码的这段时间里，馆员随时可能扫码成功。
  // 只做"状态真变了"的最小请求（复用 myEnrollments 一次查询），成功即停止并跳转。
  _startWatch() {
    this._stopWatch()
    if (!this._enrollmentId) return
    this._watch = setInterval(() => this._tick(), WATCH_INTERVAL_MS)
  },

  _stopWatch() {
    if (this._watch) {
      clearInterval(this._watch)
      this._watch = null
    }
  },

  async _tick() {
    // 已签到 / 成功态 / 无券 → 不再轮询（省电省请求）
    if (this.data.checkinSuccess || this.data.checkedIn || !this.data.enrollment) return
    // 在途守卫：网络慢时 3s 定时器会叠请求，叠了既费流量也可能"迟到的那次"覆盖新状态
    if (this._ticking) return
    this._ticking = true
    try {
      const list = await api.myEnrollments(this._childId)
      const mine = (list || []).find((m) => Number(m.id) === this._enrollmentId)
      if (mine && mine.status === 'checked_in') this._onCheckedIn(mine)
    } catch (e) {
      // 静默：轮询失败不打扰家长，下一轮再试
    } finally {
      this._ticking = false
    }
  },

  // 签到成功：先给"看得见"的反馈（家长此时正把手机递给馆员，toast 太轻），再跳活动详情
  _onCheckedIn(mine) {
    this._stopWatch()
    const t = mine.checked_in_at ? String(mine.checked_in_at).replace('T', ' ').slice(11, 16) : ''
    wx.vibrateShort && wx.vibrateShort({ fail: () => {} })
    this.setData({
      checkinSuccess: true,
      checkedIn: true,
      checkedInTime: t,
      brightnessBoosted: false,
    })
    this._restoreBrightness()
    setTimeout(() => this._goActivityDetail(mine.activity_id), SUCCESS_HOLD_MS)
  },

  // 回活动详情：从详情页进来的就返回（不叠页），从列表进来的就 redirect（替换本页），
  // 保证"退出后落在活动详情"而不是退回列表
  _goActivityDetail(activityId) {
    const url = `/pages/activity-pkg/activity-detail/activity-detail?id=${activityId}`
      + `&child_id=${this._childId}&child_name=${encodeURIComponent(this.data.childName)}`
    const pages = (typeof getCurrentPages === 'function') ? getCurrentPages() : []
    const prev = pages.length > 1 ? pages[pages.length - 2] : null
    const fromDetail = prev && prev.route === 'pages/activity-pkg/activity-detail/activity-detail'
    if (fromDetail) {
      wx.navigateBack({ fail: () => wx.redirectTo({ url }) })
    } else {
      wx.redirectTo({ url, fail: () => wx.navigateBack({}) })
    }
  },

  async load() {
    this.setData({ loading: true, loadError: false })
    try {
      const list = await api.myEnrollments(this._childId)
      const mine = (list || []).find((m) => Number(m.id) === this._enrollmentId)
      if (!mine) {
        // 报名被取消/退款后旧入口仍在（如从历史列表进入）→ 明确告知而不是空白页
        this.setData({
          loadError: true,
          errorTitle: '入场券已失效',
          errorDesc: '该报名已取消或已退款，入场券不再有效',
        })
        return
      }
      if (TICKET_STATUSES.indexOf(mine.status) === -1) {
        this.setData({
          loadError: true,
          errorTitle: '入场券尚未生效',
          errorDesc: mine.status === 'pending_payment'
            ? '待馆员确认收款后入场券生效，请到店咨询'
            : '当前报名状态不支持出示入场券',
        })
        return
      }
      const fmt = (t) => (t ? String(t).replace('T', ' ').slice(0, 16) : '')
      const e = { ...mine, activity_start_at: fmt(mine.activity_start_at) }
      this.setData({
        enrollment: e,
        activityTitle: mine.activity_title || '',
        startAt: e.activity_start_at,
        checkedIn: mine.status === 'checked_in',
        checkedInTime: mine.checked_in_at ? String(mine.checked_in_at).replace('T', ' ').slice(11, 16) : '',
      }, () => {
        this._scheduleTicketDraw(e)
        // 已签到的不需要调亮（没有码要扫了）；未签到的才调亮
        if (!this.data.checkedIn) this._boostBrightness()
      })
    } catch (e) {
      // F-M11 族：加载失败必须有错误态 + 重试，不许整页空白
      this.setData({ loadError: true, errorTitle: '入场券加载失败', errorDesc: '请检查网络后重试' })
    }
    finally { this.setData({ loading: false }) }
  },

  // 失败态的"重试"：券还在就重试，券失效就回列表（比反复重试一个必然失败的请求合理）
  onBackToList() {
    if (this.data.enrollment) return this.load()
    wx.navigateBack({
      fail: () => wx.reLaunch({ url: '/pages/activity-pkg/activity-list/activity-list' }),
    })
  },

  // 出图调度：画布是"隐藏的渲染后端"，出图异步，画完再导出成图片。
  // 首次若在布局未稳时查询节点会拿不到尺寸 → 补一次；出图本身与布局无关，不存在错位。
  _scheduleTicketDraw(e) {
    if (!e || !e.ticket_code) return
    this._clearDrawTimers()
    this._drawTimers = [0, 300].map((ms) => setTimeout(() => this._renderTicket(e), ms))
  },

  _clearDrawTimers() {
    if (this._drawTimers) {
      this._drawTimers.forEach(clearTimeout)
      this._drawTimers = []
    }
  },

  // 出示入场券时把屏幕亮度拉满（扫码枪成功率）+ 保持常亮（出示码时别息屏）。
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
  // 画布只是"渲染后端"（移出可视区），展示一律用 canvasToTempFilePath 出来的 <image>：
  // `canvas type="2d"` 是原生层，首帧坐标早于布局稳定时会停在旧位置且不自愈
  // （实测二维码画到 hero 上、条形码画到按钮附近 = 用户报的"遮挡"），当图片显示就没有这个问题。
  _renderTicket(e) {
    if (!e || !e.ticket_code) return
    const code = e.ticket_code
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

  copyTicket() {
    const e = this.data.enrollment
    if (!e) return
    wx.setClipboardData({ data: e.ticket_code })
  },
})
