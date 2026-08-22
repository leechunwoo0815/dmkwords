// pages/shelf/shelf.js — 书架（收藏夹 WM8 交付，先占位）
const session = require('../../utils/session')

Page({
  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 2 })
    }
  },
})
