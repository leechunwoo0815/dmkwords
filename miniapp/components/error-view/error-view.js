// frontend/components/error-view/error-view.js
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
    // 人工兜底联系方式（门店电话/客服微信），由页面传入；为空优雅隐藏。
    // 组件自身不发请求（/venue/contact 端点后端未实现，域F F-M13 裁定剥离）。
    contactText: { type: String, value: '' },
  },
  data: {
    // 图标走自家资产（/icons/ui/*.png）：emoji 三端渲染不一致、颜色与令牌无关，
    // 且 R13c 已把"JS 数据里的 emoji"纳入门禁（2026-09-21）
    iconUrl: '/icons/ui/warning.png',
  },
  observers: {
    'type': function(type) {
      const icons = {
        error: '/icons/ui/warning.png',
        network: '/icons/ui/globe.png',
        empty: '/icons/ui/empty.png',
        permission: '/icons/ui/lock.png',
      };
      this.setData({ iconUrl: icons[type] || '/icons/ui/warning.png' });
    }
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
