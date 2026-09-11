const api = require('../utils/api')

Component({
  data: {
    selected: 0,
    // WM15-R7：阅读圈红点（= 消息中心 circle.liked 未读数，同源；0=不显示）
    circleDot: 0,
    color: '#6B5B5B',
    selectedColor: '#FF6B35',
    list: [
      {
        pagePath: '/pages/index/index',
        text: '首页',
        iconPath: '/icons/home.png',
        selectedIconPath: '/icons/home-active.png',
      },
      {
        pagePath: '/pages/books/books',
        text: '图书馆',
        iconPath: '/icons/book.png',
        selectedIconPath: '/icons/book-active.png',
      },
      {
        pagePath: '/pages/shelf/shelf',
        text: '书架',
        iconPath: '/icons/shelf.png',
        selectedIconPath: '/icons/shelf-active.png',
      },
      {
        pagePath: '/pages/circle/circle',
        text: '阅读圈',
        iconPath: '/icons/circle.png',
        selectedIconPath: '/icons/circle-active.png',
      },
      {
        pagePath: '/pages/member/member',
        text: '我的',
        iconPath: '/icons/me.png',
        selectedIconPath: '/icons/me-active.png',
      },
    ],
  },

  attached() {
    this.refreshBadge()
  },

  methods: {
    // 红点刷新：口径同消息中心未读（后端 unread_by_scene.circle_liked）——禁自建第二套计数
    async refreshBadge() {
      if (!wx.getStorageSync('token')) return
      try {
        const r = await api.notifications(1, 1)
        const n = (r.unread_by_scene && r.unread_by_scene.circle_liked) || 0
        this.setData({ circleDot: n > 99 ? 99 : n })
      } catch (e) { /* 静默：红点失败不影响导航 */ }
    },

    switchTab(e) {
      const { path, index } = e.currentTarget.dataset
      wx.switchTab({ url: path })
      this.setData({ selected: index })
    },
  },
})
