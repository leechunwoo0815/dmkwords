// frontend/components/error-view/error-view.js
const req = require('../../utils/request')

Component({
  properties: {
    type: { type: String, value: 'error' }, // error | network | empty | permission
    visible: { type: Boolean, value: true },
    title: { type: String, value: '' },
    desc: { type: String, value: '' },
    showRetry: { type: Boolean, value: true },
    showBack: { type: Boolean, value: false },
    retryText: { type: String, value: '重试' },
    backText: { type: String, value: '返回' },
  },
  data: {
    icon: '😔',
    contactText: '', // G2 人工兜底联系方式（门店电话/客服微信），为空优雅隐藏
  },
  observers: {
    'type': function(type) {
      const icons = { error: '😔', network: '📡', empty: '📭', permission: '🔒' };
      this.setData({ icon: icons[type] || '😔' });
    }
  },
  lifetimes: {
    attached() {
      // G2/P2-5：错误页附人工兜底联系方式（配置为空时静默隐藏）
      req.get('/venue/contact', null, { auth: false })
        .then((c) => {
          if (!c) return
          const parts = []
          if (c.phone) parts.push(`门店电话：${c.phone}`)
          if (c.wechat) parts.push(`客服微信：${c.wechat}`)
          if (parts.length) this.setData({ contactText: parts.join('　') })
        })
        .catch(() => {})
    },
  },
  methods: {
    onRetry() { this.triggerEvent('onRetry'); },
    onBack() {
      try {
        var pages = getCurrentPages()
        if (pages.length > 1) {
          wx.navigateBack()
        } else {
          wx.switchTab({ url: '/pages/index/index' })
        }
      } catch (e) {
        wx.switchTab({ url: '/pages/index/index' })
      }
    },
  }
});
