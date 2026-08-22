Component({
  data: {
    selected: 0,
    color: '#6f685a',
    selectedColor: '#2c4a6e',
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
        pagePath: '/pages/member/member',
        text: '我的',
        iconPath: '/icons/me.png',
        selectedIconPath: '/icons/me-active.png',
      },
    ],
  },

  methods: {
    switchTab(e) {
      const { path, index } = e.currentTarget.dataset
      wx.switchTab({ url: path })
      this.setData({ selected: index })
    },
  },
})
