// miniapp/utils/session.js — 家长/孩子会话（WM6）
function getParent() { return wx.getStorageSync('parent') || null }
function getChildren() { return wx.getStorageSync('children') || [] }
function getCurrentChild() {
  const children = getChildren()
  const id = wx.getStorageSync('currentChildId')
  return children.find((c) => c.id === id) || children[0] || null
}
function setCurrentChild(id) { wx.setStorageSync('currentChildId', id) }
// WM14-B：局部更新家长资料（改称呼后同步本地缓存，避免重登才生效）
function patchParent(patch) {
  const p = getParent() || {}
  wx.setStorageSync('parent', { ...p, ...patch })
}
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
module.exports = { getParent, getChildren, getCurrentChild, setCurrentChild, patchParent, isLoggedIn, ensureLogin, logout }
