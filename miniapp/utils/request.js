// frontend/utils/request.js — 统一请求封装（MP-020/MP-033 优化版）

function request(method, path, data, options = {}) {
  const app = getApp()
  const { auth = true, showLoading = false, showError = true, params } = options

  // 处理 query params
  let url = `${app.globalData.baseURL}${path}`
  if (params && typeof params === 'object') {
    const qs = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join('&')
    if (qs) url += (url.includes('?') ? '&' : '?') + qs
  }

  if (showLoading) {
    wx.showLoading({ title: '加载中...', mask: true })
  }

  // MP-020: 网络状态检测
  return new Promise((resolve, reject) => {
    wx.getNetworkType({
      success(net) {
        if (net.networkType === 'none') {
          if (showLoading) wx.hideLoading()
          wx.showToast({ title: '当前无网络连接', icon: 'none', duration: 3000 })
          reject(new Error('无网络'))
          return
        }

        doRequest(app, method, url, data, options, showLoading, resolve, reject)
      },
      fail() {
        // 网络检测失败，直接发请求
        doRequest(app, method, url, data, options, showLoading, resolve, reject)
      }
    })
  })
}

function doRequest(app, method, url, data, options, showLoading, resolve, reject) {
  const { auth = true, showError = true } = options
  const headers = { 'Content-Type': 'application/json' }

  if (auth && app.globalData.token) {
    headers['Authorization'] = `Bearer ${app.globalData.token}`
  }

  wx.request({
    url,
    method,
    data,
    header: headers,
    timeout: method === 'POST' || method === 'PUT' ? 15000 : 10000,
    success(res) {
      if (showLoading) wx.hideLoading()

      if (res.statusCode === 401) {
        var pages = getCurrentPages()
        var currentRoute = pages.length > 0 ? pages[pages.length - 1].route : ''
        if (currentRoute && currentRoute.indexOf('login') !== -1) {
          reject(new Error('登录已过期'))
          return
        }
        // 清除所有用户相关状态
        wx.removeStorageSync('token')
        app.globalData.token = ''
        app.globalData.userInfo = null
        app.globalData.currentChild = null
        wx.reLaunch({ url: '/pages/login/login' })
        reject(new Error('登录已过期'))
        return
      }

      if (res.statusCode === 403) {
        // 带 error_code 的 403（如 consent_required）交给调用方处理 UX，不弹通用提示
        const errCode = (res.data && res.data.error_code) || null
        if (!errCode) {
          wx.showToast({ title: '无权限执行此操作', icon: 'none' })
        }
        const err403 = new Error((res.data && res.data.detail) || '无权限')
        err403.errorCode = errCode
        err403.statusCode = 403
        reject(err403)
        return
      }

      if (res.statusCode >= 200 && res.statusCode < 300) {
        resolve(res.data)
      } else {
        // MP-033: 统一错误消息
        const msg = res.data?.detail || res.data?.message || `请求失败 (${res.statusCode})`
        if (showError) {
          wx.showToast({ title: msg, icon: 'none', duration: 2000 })
        }
        reject(new Error(msg))
      }
    },
    fail(err) {
      if (showLoading) wx.hideLoading()
      // MP-020: 网络异常区分
      if (showError) {
        wx.showToast({ title: '网络异常，请检查网络连接', icon: 'none', duration: 3000 })
      }
      reject(err)
    }
  })
}

module.exports = {
  get(path, data, options) { return request('GET', path, data, options) },
  post(path, data, options) { return request('POST', path, data, options) },
  put(path, data, options) { return request('PUT', path, data, options) },
  del(path, data, options) { return request('DELETE', path, data, options) },
}
