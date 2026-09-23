// miniapp/utils/session.js — 家长/孩子会话（WM6）
function getParent() { return wx.getStorageSync('parent') || null }
function getChildren() { return wx.getStorageSync('children') || [] }
function getCurrentChild() {
  const children = getChildren()
  const id = wx.getStorageSync('currentChildId')
  // 宽松比较（两侧转字符串）：切换时 id 来自 `data-id` 的 dataset，**类型随基础库不定**
  // （实测可能是 "10" 字符串）。此前 `c.id === id` 严格比较，类型不匹配即静默回落到
  // children[0] —— 表现就是"切了孩子，页面还显示上一个孩子的数据"（2026-09-21 用户报障同族）。
  return children.find((c) => String(c.id) === String(id)) || children[0] || null
}
function setCurrentChild(id) {
  // 写入口径统一成数字（页面与接口都按 number 用）；纯数字串转数字，其余原样存
  wx.setStorageSync('currentChildId', typeof id === 'string' && /^\d+$/.test(id) ? Number(id) : id)
}
// WM14-B：局部更新家长资料（改称呼后同步本地缓存，避免重登才生效）
function patchParent(patch) {
  const p = getParent() || {}
  wx.setStorageSync('parent', { ...p, ...patch })
}
function isLoggedIn() { return !!wx.getStorageSync('token') }

// 2026-09-16：从服务端刷新孩子列表并回写本地缓存。
// 为什么需要：本地 `children` 只是**登录那一刻的快照**，而阅读圈/点赞墙/排行榜走
// 服务端实时数据——店主在后台改了孩子头像（或会员到期）后，两端就会不一致
// （用户报障「点赞的头像跟我的页面的头像不匹配」）。app 每次前台 + 我的页 onShow 拉一次。
// 失败静默（保留旧缓存，不能因刷新失败把页面打空）；并发去重 + 30 秒节流。
let _lastRefreshAt = 0
let _inflight = null
function refreshChildren(force = false) {
  if (!isLoggedIn()) return Promise.resolve(false)
  if (_inflight) return _inflight // 并发去重：同一次请求共享给多个调用方
  if (!force && Date.now() - _lastRefreshAt < 30000) return Promise.resolve(false)
  _lastRefreshAt = Date.now()
  let succeeded = false
  _inflight = (async () => {
    try {
      const api = require('./api')
      // **必须带超时**：wx.request/getNetworkType 在个别环境下会既不 success 也不 fail
      // （实测：进了 request util 但 promise 永不 settle）。没有超时的话 _inflight 锁
      // 会被永久占住 → 后续所有刷新静默失效、页面 await 卡死（2026-09-16 踩过）。
      const res = await Promise.race([
        api.myChildren(),
        new Promise((resolve) => setTimeout(() => resolve(null), 6000)),
      ])
      const children = (res && res.children) || []
      if (!children.length) return false // F-L17：无孩子不动缓存（空数组会打空切换栏）
      wx.setStorageSync('children', children)
      // 缓存里已没有的 currentChildId（如孩子被删）→ 归位到第一个，避免走兜底
      const cur = wx.getStorageSync('currentChildId')
      if (!children.some((c) => c.id === cur)) wx.setStorageSync('currentChildId', children[0].id)
      succeeded = true
      return true
    } catch (e) {
      return false // 网络异常保留旧缓存（绝不因刷新失败把页面打空）
    } finally {
      _inflight = null
      // 失败/超时 → 清零时间戳，下次 onShow 立刻重试；成功才吃 30 秒节流
      if (!succeeded) _lastRefreshAt = 0
    }
  })()
  return _inflight
}

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
module.exports = { getParent, getChildren, getCurrentChild, setCurrentChild, patchParent, isLoggedIn, ensureLogin, refreshChildren, logout }
