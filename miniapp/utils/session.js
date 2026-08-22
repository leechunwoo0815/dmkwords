// miniapp/utils/session.js — 家长/孩子会话（WM6）
function getParent() { return wx.getStorageSync('parent') || null }
function getChildren() { return wx.getStorageSync('children') || [] }
function getCurrentChild() {
  const children = getChildren()
  const id = wx.getStorageSync('currentChildId')
  return children.find((c) => c.id === id) || children[0] || null
}
function setCurrentChild(id) { wx.setStorageSync('currentChildId', id) }
function isLoggedIn() { return !!wx.getStorageSync('token') }
function ensureLogin() {
  if (!isLoggedIn()) {
    wx.reLaunch({ url: '/pages/login/login' })
    return false
  }
  return true
}
function logout() {
  ;['token', 'parent', 'children', 'currentChildId'].forEach((k) => wx.removeStorageSync(k))
  const app = getApp()
  app.globalData.token = ''
  app.globalData.userInfo = null
  app.globalData.currentChild = null
}
module.exports = { getParent, getChildren, getCurrentChild, setCurrentChild, isLoggedIn, ensureLogin, logout }
