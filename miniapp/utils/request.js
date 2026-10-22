// frontend/utils/request.js — 统一请求封装（MP-020/MP-033 优化版）
//
// `options.withHeaders`（P3 第一百一十六批）：置 true 时 resolve `{data, headers, statusCode}`
// 而不是裸 `data`。**为什么需要**：列表类接口的硬上限走**响应头**下发
// （`X-List-Limit` / `X-List-Truncated`，见 `backend/common/list_meta.py` 的 P2-44 说明），
// 而这一层原先把 `wx.request` 的 `res.headers` 直接丢掉 → 小程序**永远读不到"已截断"**，
// 家长看到 50 条订单会以为"这就是全部订单"（与管理端同一类"看起来是全部"的截断幻觉）。
// 默认 false ⇒ 既有 200+ 处调用零影响。

function request(method, path, data, options = {}) {
  const app = getApp()
  const { auth = true, showLoading = false, showError = true, params } = options

  // 2026-09-16：getApp() 在 App.onLaunch/onShow 执行期间尚未就绪（微信经典坑），
  // 此时 app 为 undefined，原代码会在下一行抛「Cannot read properties of undefined
  // (reading 'globalData')」——错误信息完全指不到真因。这里显式拒绝，便于定位。
  if (!app) return Promise.reject(new Error('应用未就绪（勿在 App.onLaunch/onShow 里发请求）'))

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
        // 带 error_code 的 403（如 audio.off_shelf）交给调用方处理 UX，不弹通用提示
        const errCode = (res.data && res.data.error_code) || null
        // P1 #12（2026-10-15 大审查）：无 error_code 的 403 **必须显示服务端 detail**——
        // 原先一律弹「无权限执行此操作」，于是"该书已下架，暂不能播放（已听进度保留）"
        // 与"会员已过期，只能收听手中在借的图书音频"这类**真实原因**家长永远看不到
        // （真正的原因藏在 detail 里，前端把它丢了）。
        const msg403 = (res.data && res.data.detail) || ''
        if (!errCode) {
          wx.showToast({ title: msg403 || '无权限执行此操作', icon: 'none' })
        }
        const err403 = new Error(msg403 || '无权限')
        err403.errorCode = errCode
        err403.statusCode = 403
        reject(err403)
        return
      }

      if (res.statusCode >= 200 && res.statusCode < 300) {
        // withHeaders：把响应头一并交给调用方（列表截断信号 `X-List-Truncated` 的唯一读取口）
        resolve(
          options.withHeaders
            ? { data: res.data, headers: res.headers || {}, statusCode: res.statusCode }
            : res.data
        )
      } else {
        // MP-033 + F-M2/T26：FastAPI 422 校验错的 detail 是数组 [{loc,msg,type}]，
        // 取首条 msg 显示（对齐 admin-web client.ts 同款修复），否则 toast 变 [object Object]
        let detail = res.data?.detail ?? res.data?.message ?? null
        if (Array.isArray(detail)) {
          detail = detail[0]?.msg || detail[0]?.message || null
        }
        const msg = detail || `请求失败 (${res.statusCode})`
        if (showError) {
          wx.showToast({ title: msg, icon: 'none', duration: 2000 })
        }
        // P2-45（2026-10-20 第八轮）：**错误对象必须带状态码**。原先非 2xx 一律
        // `new Error(msg)`（不带 statusCode）→ 页面分不清"业务空/未入会（422）"与
        // "请求真失败（5xx）"，只能一律按空/0 渲染（"失败态伪装成空"的根因）。
        const httpErr = new Error(msg)
        httpErr.statusCode = res.statusCode
        reject(httpErr)
      }
    },
    fail(err) {
      if (showLoading) wx.hideLoading()
      // MP-020: 网络异常区分
      if (showError) {
        wx.showToast({ title: '网络异常，请检查网络连接', icon: 'none', duration: 3000 })
      }
      // P2-45：网络层失败标记 statusCode=0（页面据此走"失败态 + 重试"，不按空/0 渲染）
      if (err && typeof err === 'object') {
        err.statusCode = 0
        err.isNetwork = true
      }
      reject(err)
    }
  })
}

/** 读列表截断信号（P2-44）：`{limit, truncated}`。
 *
 * 头名做**大小写不敏感**查找：HTTP 头本身大小写不敏感，而微信基础库/开发者工具
 * 与真机回传的键风格并不一致（`X-List-Truncated` / `x-list-truncated`）。判 `truncated`
 * 只在值恰好是 `true` 时为真——取不到头（老版本/代理吞头）时按"未知"处理，
 * 退回"不提示"，绝不把未知说成"没有更多"。
 */
function listMeta(headers) {
  const h = headers || {}
  const pick = (name) => {
    const key = Object.keys(h).find((k) => k.toLowerCase() === name.toLowerCase())
    return key ? h[key] : ''
  }
  return {
    limit: Number(pick('X-List-Limit')) || 0,
    truncated: String(pick('X-List-Truncated')).toLowerCase() === 'true',
  }
}

module.exports = {
  listMeta,
  get(path, data, options) { return request('GET', path, data, options) },
  post(path, data, options) { return request('POST', path, data, options) },
  put(path, data, options) { return request('PUT', path, data, options) },
  del(path, data, options) { return request('DELETE', path, data, options) },
}
