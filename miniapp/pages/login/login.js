// pages/login/login.js — 手机号+验证码登录（开发期验证码固定 1234）
const api = require('../../utils/api')
const session = require('../../utils/session')

Page({
  data: {
    phone: '',
    code: '',
    submitting: false,
    agreed: true,
  },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }) },
  onCodeInput(e) { this.setData({ code: e.detail.value }) },
  toggleAgreed() { this.setData({ agreed: !this.data.agreed }) },

  async onLogin() {
    const { phone, code, agreed, submitting } = this.data
    if (submitting) return
    if (!/^\d{11}$/.test(phone)) {
      wx.showToast({ title: '请输入 11 位手机号', icon: 'none' }); return
    }
    if (!/^\d{4}$/.test(code)) {
      wx.showToast({ title: '请输入 4 位验证码', icon: 'none' }); return
    }
    if (!agreed) {
      wx.showToast({ title: '请先同意隐私政策', icon: 'none' }); return
    }
    this.setData({ submitting: true })
    try {
      const res = await api.login(phone, code)
      const app = getApp()
      app.globalData.token = res.token
      app.globalData.userInfo = res.parent
      wx.setStorageSync('token', res.token)
      wx.setStorageSync('parent', res.parent)
      wx.setStorageSync('children', res.children || [])
      if (res.children && res.children.length) {
        const current = session.getCurrentChild()
        if (!current) wx.setStorageSync('currentChildId', res.children[0].id)
      }
      wx.showToast({ title: '登录成功', icon: 'success' })
      setTimeout(() => wx.reLaunch({ url: '/pages/index/index' }), 600)
    } catch (e) {
      // request.js 已 toast 错误详情
    } finally {
      this.setData({ submitting: false })
    }
  },
})
