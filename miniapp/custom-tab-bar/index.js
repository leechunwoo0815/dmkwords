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
    // 首次引导（首页实例在 App 启动时创建）
    this.refreshBadge()
  },

  // fix34 R0：微信自定义 tabBar **每个 tab 页各持一个实例**，只在 attached 拉一次会让
  // 红点只活在首页那个实例上（切到别的 tab 就没数据、回首页又"恢复"）。
  // pageLifetimes.show 随**宿主页面每次显示**触发 → 每个实例各自刷新，与实例模型无关。
  pageLifetimes: {
    show() {
      this.refreshBadge()
    },
  },

  methods: {
    // 红点刷新：口径同消息中心未读（后端 unread_by_scene.circle_liked）——禁自建第二套计数
    async refreshBadge() {
      if (!wx.getStorageSync('token')) return
      try {
        const r = await api.notifications(1, 1)
        const n = (r.unread_by_scene && r.unread_by_scene.circle_liked) || 0
        this.setData({ circleDot: n > 99 ? 99 : n })
      } catch (e) {
        // fix34 R0：失败**保留旧值**（原先静默且不清值，导致"到底拉没拉到"无法判断）
        console.warn('[tabbar] 红点刷新失败', e)
      }
    },

    switchTab(e) {
      const { path, index } = e.currentTarget.dataset
      wx.switchTab({ url: path })
      this.setData({ selected: index })
    },
  },
})
