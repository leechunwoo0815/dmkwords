// pages/login/login.js — 微信一键登录（主）+ 手机号验证码（兜底/首次绑定）
// 2026-10-08 接线（审查 P0-1）：生产环境固定验证码关闭后，这里是唯一的入口。
//   ① 主通道：wx.login → /login/wechat → 已绑 → 直接进首页；未绑 → 绑定面板（手机号 + 短信码）
//   ② 兜底：手机号 + 短信验证码（/sms/send + /login），开发期固定码 1234 仍可用
const api = require('../../utils/api')
const { loadConfigs, getConfigs } = require('../../utils/configs')

Page({
  data: {
    mode: 'wechat', // wechat=微信一键登录 | bind=首次绑定 | sms=手机号验证码登录
    phone: '',
    code: '',
    bindTicket: '',
    submitting: false,
    agreed: true,
    sending: false,
    countdown: 0,
    // 2026-10-12 拍板（P1）：短信通道开关由服务端下发——关闭时**隐藏手机号入口**
    // （不允许出现"页面能点、验证码永远收不到"的假入口）
    // W7-16（2026-10-28）：默认改为 **false**（fail-closed）——原先初值 true 且请求失败空转，
    // 服务端关闭短信时若取数失败会露出假入口。微信主通道不受影响。
    smsEnabled: false,
    loginError: '',  // P2-52：登录失败在页面内也留痕（toast 会消失，家长回来能看到原因）
    cfg: getConfigs(),  // 业务口径（后端下发；倒计时秒数 P2-73）
  },

  async onLoad() {
    // 口径先落地（登录前就要用：验证码倒计时），再取登录通道开关
    const { configs } = await loadConfigs()
    this.setData({ cfg: configs })
    api.getLoginOptions().then((res) => {
      if (res && typeof res.sms_enabled === 'boolean') {
        this.setData({ smsEnabled: res.sms_enabled })
      }
    }).catch(() => {
      // W7-16：取不到开关就保持隐藏（宁可少一个入口，也不给假入口）
      this.setData({ smsEnabled: false })
    })
  },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }) },
  onCodeInput(e) { this.setData({ code: e.detail.value }) },
  toggleAgreed() { this.setData({ agreed: !this.data.agreed }) },

  // 微信一键登录（主通道）
  async onWechatLogin() {
    if (this.data.submitting) return
    this.setData({ loginError: '' })
    if (!this.data.agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this.setData({ submitting: true })
    try {
      const wxCode = await this._wxLoginCode()
      const res = await api.loginByWechat(wxCode)
      if (res && res.need_bind) {
        // 首次：拿到绑定凭证，切到绑定面板（手机号需已在馆建档）
        this.setData({ mode: 'bind', bindTicket: res.bind_ticket })
        wx.showToast({ title: '首次登录，请验证手机号', icon: 'none' })
        return
      }
      this._enterApp(res)
    } catch (e) {
      // P2-52（第八轮）：**取 code 失败原先完全静默**（"点了没反应"型入口故障）。
      // request.js 只覆盖 HTTP 失败；wx.login 自身失败/无 code 在这里必须自己讲清楚。
      const msg = (e && e.message) || '微信登录失败，请重试'
      wx.showToast({ title: msg, icon: 'none', duration: 2500 })
      this.setData({ loginError: msg })
    } finally {
      this.setData({ submitting: false })
    }
  },

  // 首次绑定：绑定凭证 + 手机号 + 短信码
  async onBind() {
    const { phone, code, bindTicket, submitting, agreed } = this.data
    if (submitting) return
    if (!/^\d{11}$/.test(phone)) {
      wx.showToast({ title: '请输入 11 位手机号', icon: 'none' }); return
    }
    if (!/^\d{4,6}$/.test(code)) {
      wx.showToast({ title: '请输入验证码', icon: 'none' }); return
    }
    if (!agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this.setData({ submitting: true })
    try {
      const res = await api.bindByWechat(bindTicket, phone, code)
      this._enterApp(res)
    } catch (e) {
      // request.js 已 toast
    } finally {
      this.setData({ submitting: false })
    }
  },

  // 短信兜底登录
  async onSmsLogin() {
    const { phone, code, submitting, agreed } = this.data
    if (submitting) return
    if (!/^\d{11}$/.test(phone)) {
      wx.showToast({ title: '请输入 11 位手机号', icon: 'none' }); return
    }
    if (!/^\d{4,6}$/.test(code)) {
      wx.showToast({ title: '请输入验证码', icon: 'none' }); return
    }
    if (!agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this.setData({ submitting: true })
    try {
      const res = await api.login(phone, code)
      this._enterApp(res)
    } catch (e) {
      // request.js 已 toast
    } finally {
      this.setData({ submitting: false })
    }
  },

  // 发送验证码（绑定/登录共用；同号重发间隔与每日上限都在服务端，倒计时读下发口径）
  async onSendCode() {
    const { phone, sending, mode } = this.data
    if (sending || this.data.countdown > 0) return
    if (!/^\d{11}$/.test(phone)) {
      wx.showToast({ title: '请先填 11 位手机号', icon: 'none' }); return
    }
    this.setData({ sending: true })
    try {
      await api.sendSms(phone, mode === 'bind' ? 'bind' : 'login')
      wx.showToast({ title: '验证码已发送', icon: 'success' })
      this._startCountdown()
    } catch (e) {
      // request.js 已 toast（含"发送太频繁，请 N 秒后再试"）
    } finally {
      this.setData({ sending: false })
    }
  },

  switchToSms() { this.setData({ mode: 'sms', code: '' }) },
  switchToWechat() { this.setData({ mode: 'wechat', code: '' }) },

  // 进 app：写登录态（与既有口径一致）
  _enterApp(res) {
    const app = getApp()
    app.globalData.token = res.token
    app.globalData.userInfo = res.parent
    wx.setStorageSync('token', res.token)
    wx.setStorageSync('parent', res.parent)
    wx.setStorageSync('children', res.children || [])
    if (res.children && res.children.length) {
      // 清场重建后 id 会重排：**强制覆盖**，不沿用旧的 currentChildId（否则指向别的孩子）
      wx.removeStorageSync('currentChildId')
      wx.setStorageSync('currentChildId', res.children[0].id)
    }
    wx.showToast({ title: '登录成功', icon: 'success' })
    setTimeout(() => wx.reLaunch({ url: '/pages/index/index' }), 600)
  },

  _wxLoginCode() {
    return new Promise((resolve, reject) => {
      wx.login({
        success: (r) => (r && r.code ? resolve(r.code) : reject(new Error('微信登录失败'))),
        fail: () => reject(new Error('微信登录失败，请重试')),
      })
    })
  },

  _startCountdown() {
    // P2-73（第八轮）：倒计时必须与服务端同号重发间隔**同一份值**（配置中心
    // `sms_send_interval_seconds`，种子 90 秒）——原先写死 60：家长 60 秒后点发送
    // 必被服务端拒（"发送太频繁"），看起来像 bug。口径由 /api/miniapp/configs 下发。
    this.setData({ countdown: this.data.cfg.sms_send_interval_seconds })
    // R11-d2/M12：定时器存到实例上并在 onUnload 清理（原先局部 timer，离页后继续跑）
    if (this._countdownTimer) clearInterval(this._countdownTimer)
    this._countdownTimer = setInterval(() => {
      const next = this.data.countdown - 1
      if (next <= 0) {
        clearInterval(this._countdownTimer)
        this._countdownTimer = null
        this.setData({ countdown: 0 })
        return
      }
      this.setData({ countdown: next })
    }, 1000)
  },

  onUnload() {
    if (this._countdownTimer) {
      clearInterval(this._countdownTimer)
      this._countdownTimer = null
    }
  },

  goServiceAgreement() {
    wx.navigateTo({ url: '/pages/agreement/service-agreement/service-agreement' })
  },

  goPrivacy() {
    wx.navigateTo({ url: '/pages/agreement/privacy-policy/privacy-policy' })
  },
})
