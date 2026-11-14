// pages/login/login.js — 微信一键登录（主）+ 微信手机号快速验证（首次绑定）
// 2026-10-08 接线（审查 P0-1）：生产环境固定验证码关闭后，这里是唯一的入口。
//   ① 主通道：wx.login → /login/wechat → 已绑 → 直接进首页；
//      未绑 → 绑定面板 → getPhoneNumber → /login/wechat/phone（2026-10-29 短信退役）
//   ② 短信兼容：仅 SMS_ENABLED=true 时显示（/sms/send + /login 与 /login/bind）
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
    // 只读演示体验（2026-11-12 微信审核整改）：由服务端 /login/options 的 demo_enabled 决定，
    // 关掉服务端开关按钮就不显示——不给"能点但一定失败"的假入口。
    demoEnabled: false,
    // 页内失败态：网络层失败原先只有一条 3 秒就消失的 toast（审核员看到的画面正是它），
    // 页面本身仍然"能用"，家属/审核员不知道自己在看一个连不上服务器的页面。
    netError: '',
    // 隐私弹窗被拒绝 / 手机号授权被取消：显示固定说明区 + 三个出口（重试/体验/退出）
    privacyBlocked: false,
    loginError: '',  // P2-52：登录失败在页面内也留痕（toast 会消失，家长回来能看到原因）
    cfg: getConfigs(),  // 业务口径（后端下发；倒计时秒数 P2-73）
  },

  async onLoad() {
    this._load(false)
  },

  // 口径先落地（登录前就要用：验证码倒计时），再取登录通道开关。
  // force=true：重试时**跳过 60 秒失败冷却**（utils/configs.js 的缓存口径），否则点重试等于没点。
  async _load(force) {
    const { configs, failed } = await loadConfigs(force)
    this.setData({ cfg: configs })
    if (failed) this.setData({ netError: '网络异常，请检查网络连接后重试' })
    api.getLoginOptions().then((res) => {
      if (res && typeof res.sms_enabled === 'boolean') {
        this.setData({ smsEnabled: res.sms_enabled })
      }
      if (res && typeof res.demo_enabled === 'boolean') {
        this.setData({ demoEnabled: res.demo_enabled })
      }
      this.setData({ netError: '' })
    }).catch((e) => {
      // W7-16：取不到开关就保持隐藏（宁可少一个入口，也不给假入口）
      // 2026-11-12：同时置页内失败态——审核驳回时页面只有一条「网络异常」toast，
      // 看不出是"网络断了"还是"接口地址配错了"，家属也无从重试。
      this.setData({ smsEnabled: false, netError: '网络异常，请检查网络连接后重试' })
      console.error('[login] login/options 失败', (e && e.errMsg) || e)
    })
  },

  // 重试取数（页内失败态上的按钮）：强制刷新口径 + 重新取开关
  retryNetwork() {
    this.setData({ netError: '' })
    this._load(true)
  },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }) },
  onCodeInput(e) { this.setData({ code: e.detail.value }) },
  toggleAgreed() { this.setData({ agreed: !this.data.agreed }) },

  // 本地隐私同意留痕（版本 + 时间）。后端 consent 表属 FEAT-006，尚未实现。
  // 2026-11-06：隐私政策按实际收集范围修订，版本 v1 → v2；旧版本同意视为过期，重新记录。
  _recordPrivacyConsent() {
    if (!wx.getStorageSync('privacy_agreed') || wx.getStorageSync('privacy_agreed_version') !== 'v2') {
      wx.setStorageSync('privacy_agreed', true)
      wx.setStorageSync('privacy_agreed_version', 'v2')
      wx.setStorageSync('privacy_agreed_at', Date.now())
    }
  },

  // 微信一键登录（主通道）
  async onWechatLogin() {
    if (this.data.submitting) return
    this.setData({ loginError: '' })
    if (!this.data.agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this._recordPrivacyConsent()
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

  // 首次绑定（主通道，2026-10-29 短信退役）：微信手机号快速验证
  async onGetPhoneNumber(e) {
    if (this.data.submitting) return
    const detail = (e && e.detail) || {}
    if (!detail.code) {
      const errMsg = detail.errMsg || ''
      // 2026-11-12（审核驳回「点击取消/返回选项无效」）：拒绝隐私 / 取消授权 / 未同意隐私指引
      // 三种失败都要落到**页内固定说明区**（重试 / 体验一下 / 退出小程序），
      // 不能只弹一条 toast 然后停在原地——那正是审核员看到的"点了没反应"。
      const blocked =
        errMsg.indexOf('deny') >= 0 ||
        errMsg.indexOf('cancel') >= 0 ||
        errMsg.indexOf('privacy') >= 0
      if (blocked) {
        this.setData({
          privacyBlocked: true,
          loginError: '未同意隐私授权，手机号验证无法继续',
        })
        return
      }
      const msg = '手机号验证未完成，请重试或联系馆员'
      wx.showToast({ title: msg, icon: 'none', duration: 2500 })
      this.setData({ loginError: msg })
      return
    }
    if (!this.data.agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this._recordPrivacyConsent()
    this.setData({ submitting: true, loginError: '' })
    try {
      const res = await api.bindByWechatPhone(this.data.bindTicket, detail.code)
      this._enterApp(res)
    } catch (err) {
      const msg = (err && err.message) || '手机号绑定失败，请重试或联系馆员'
      wx.showToast({ title: msg, icon: 'none', duration: 2500 })
      this.setData({ loginError: msg })
    } finally {
      this.setData({ submitting: false })
    }
  },

  // 首次绑定（短信兼容通道）：绑定凭证 + 手机号 + 短信码
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
    this._recordPrivacyConsent()
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
    this._recordPrivacyConsent()
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

  // 只读演示体验（2026-11-12 微信审核整改）：审核员/潜在客户没有本店账号也能看到核心页面。
  // 服务端 `DEMO_LOGIN_PHONE` 关闭时端点 422，按钮本身也不会出现（demo_enabled）。
  async onDemoLogin() {
    if (this.data.submitting) return
    this.setData({ submitting: true, loginError: '', privacyBlocked: false })
    try {
      const res = await api.loginDemo()
      this._enterApp(res)
    } catch (e) {
      // 演示入口失败必须说清原因（未开启 / 演示账号未建档 / 网络）
      const msg = (e && e.message) || '演示体验暂时不可用，请稍后重试'
      wx.showToast({ title: msg, icon: 'none', duration: 2500 })
      this.setData({ loginError: msg })
    } finally {
      this.setData({ submitting: false })
    }
  },

  // 退出小程序：登录规范要求"拒绝授权后用户能正常离开"，不能把用户困在登录页
  exitMiniProgram() {
    if (wx.exitMiniProgram) {
      wx.exitMiniProgram({
        fail: () => wx.showToast({ title: '请点右上角关闭小程序', icon: 'none' }),
      })
      return
    }
    wx.showToast({ title: '请点右上角关闭小程序', icon: 'none' })
  },

  // 重新发起授权（隐私弹窗会再次出现，用户可点「同意」）
  retryAuthorize() {
    this.setData({ privacyBlocked: false, loginError: '' })
    if (this.data.mode === 'bind') return // 绑定面板：用户直接再点「微信手机号快速验证」
    this.onWechatLogin()
  },

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
    // 只读演示（2026-11-12）：标记本地演示态 —— 退出登录不再打服务端撤销接口
    // （演示家长是全店共用的一个账号，撤销会把别的审核会话一起踢掉），首页也会挂"演示模式"横幅。
    if (res.demo) {
      wx.setStorageSync('demo_mode', true)
      wx.showToast({ title: '演示模式：仅可浏览', icon: 'none', duration: 2000 })
    } else {
      wx.removeStorageSync('demo_mode')
      wx.showToast({ title: '登录成功', icon: 'success' })
    }
    setTimeout(() => wx.reLaunch({ url: '/pages/index/index' }), res.demo ? 900 : 600)
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
