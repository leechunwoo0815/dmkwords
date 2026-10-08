// pages/login/login.js — 微信一键登录（主）+ 手机号验证码（兜底/首次绑定）
// 2026-10-08 接线（审查 P0-1）：生产环境固定验证码关闭后，这里是唯一的入口。
//   ① 主通道：wx.login → /login/wechat → 已绑 → 直接进首页；未绑 → 绑定面板（手机号 + 短信码）
//   ② 兜底：手机号 + 短信验证码（/sms/send + /login），开发期固定码 1234 仍可用
const api = require('../../utils/api')

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
  },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }) },
  onCodeInput(e) { this.setData({ code: e.detail.value }) },
  toggleAgreed() { this.setData({ agreed: !this.data.agreed }) },

  // 微信一键登录（主通道）
  async onWechatLogin() {
    if (this.data.submitting) return
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
      // request.js 已 toast 错误详情
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

  // 发送验证码（绑定/登录共用；服务端有同号 60 秒与每日上限）
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
    this.setData({ countdown: 60 })
    const timer = setInterval(() => {
      const next = this.data.countdown - 1
      if (next <= 0) { clearInterval(timer); this.setData({ countdown: 0 }); return }
      this.setData({ countdown: next })
    }, 1000)
  },

  goServiceAgreement() {
    wx.navigateTo({ url: '/pages/agreement/service-agreement/service-agreement' })
  },

  goPrivacy() {
    wx.navigateTo({ url: '/pages/agreement/privacy-policy/privacy-policy' })
  },
})
