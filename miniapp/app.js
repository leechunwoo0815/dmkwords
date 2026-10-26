// frontend/app.js
// 生产环境通过 project.config.json 的编译环境注入 baseURL
const PROD_BASE_URL = 'https://api.dmkwords.cn'
// 开发版连本地后端。
// - 模拟器（platform=devtools）：http 127.0.0.1 即可（image 组件不信任自签 https）
// - 真机（ios/android）：媒体栈拦 http，必须走本地 https :8443
//   （证书 certs/server.crt，手机需安装并完全信任 ca.crt；本机 IP 变更需改 SAN 重新生成）
const _envVersion = (wx.getAccountInfoSync().miniProgram.envVersion || 'develop')
// P2-9（2026-10-09 审查）：本机 IP 变了不必改代码——控制台执行
//   wx.setStorageSync('dev_base_url', 'https://<新IP>:8443')
// 即可覆盖（模拟器与真机各一套默认值；改网络环境不再需要发版/改源码）。
const _devOverride = wx.getStorageSync('dev_base_url') || ''
const DEV_BASE_URL =
  _devOverride ||
  (wx.getSystemInfoSync().platform === 'devtools'
    ? 'http://127.0.0.1:8002'
    : 'https://192.168.18.162:8443')
const BASE_URL = _envVersion === 'develop' ? DEV_BASE_URL : PROD_BASE_URL

const session = require('./utils/session')

App({
  globalData: {
    baseURL: BASE_URL,
    token: '',
    userInfo: null,
    currentChild: null,
  },

  onShow() {
    // R11-d2/M11：与 session.js 注释承诺一致——每次前台刷新孩子列表（会员状态/头像）。
    // 原先 App 没有 onShow，首页直接读冷启动快照，后台改了会员态长期不更新。
    if (this.globalData.token) session.refreshChildren()
  },

  onLaunch() {
    wx.onError((err) => {
      console.error('[Global Error]', err)
    })
    wx.onUnhandledRejection((res) => {
      console.error('[Unhandled Rejection]', res.reason)
    })

    const savedToken = wx.getStorageSync('token')
    const savedUser = wx.getStorageSync('parent') // F-L18/T34：login.js 写入的是 parent（原读 userInfo 死 key，冷启动永 null）
    if (savedToken) {
      this.globalData.token = savedToken
      this.globalData.userInfo = savedUser || null
    }

    // 版本更新检测
    if (wx.getUpdateManager) {
      const updateManager = wx.getUpdateManager()
      updateManager.onUpdateReady(function () {
        wx.showModal({
          title: '更新提示',
          content: '新版本已准备好，是否重启应用？',
          success(res) { if (res.confirm) updateManager.applyUpdate() }
        })
      })
    }

    // 清理过期 quiz 缓存（>7天）
    try {
      const now = Date.now()
      const keys = wx.getStorageInfoSync().keys || []
      keys.forEach(key => {
        // P3（第一百一十三批）：`quiz_result_*` 原先不在清理面内（每孩每书一条、永不回收）
        if (key.startsWith('quiz_result_')) {
          try {
            const data = wx.getStorageSync(key)
            const parsed = typeof data === 'string' ? JSON.parse(data) : data
            if (parsed && parsed._ts && (now - parsed._ts > 7 * 24 * 60 * 60 * 1000)) {
              wx.removeStorageSync(key)
            }
          } catch (e) { /* silent */ }
        }
        if (key.startsWith('quiz_wrong_')) {
          const data = wx.getStorageSync(key)
          if (data && data._ts && (now - data._ts > 7 * 24 * 60 * 60 * 1000)) {
            wx.removeStorageSync(key)
          }
        }
        if (key.startsWith('mw_quiz_progress_')) {
          try {
            const data = wx.getStorageSync(key)
            const parsed = typeof data === 'string' ? JSON.parse(data) : data
            if (parsed && parsed.ts && (now - parsed.ts > 7 * 24 * 60 * 60 * 1000)) {
              wx.removeStorageSync(key)
            }
          } catch (e) { /* silent */ }
        }
      })
    } catch (e) { /* 静默 */ }

    // 网络状态检测
    wx.onNetworkStatusChange(function (res) {
      if (!res.isConnected) {
        wx.showToast({ title: '网络已断开，请检查网络连接', icon: 'none', duration: 3000 })
      }
    })

    // 微信官方隐私授权回调
    if (wx.onNeedPrivacyAuthorization) {
      wx.onNeedPrivacyAuthorization((resolve) => {
        const privacyPopup = () => {
          wx.showModal({
            title: '隐私保护指引',
            content: '在您使用 DmkWords 之前，请仔细阅读并同意《隐私保护指引》。我们将收集您的昵称、头像信息用于账号识别，并收集孩子的年龄、年级信息用于推荐合适的阅读内容。',
            confirmText: '同意',
            cancelText: '拒绝',
            success: (res) => {
              if (res.confirm) {
                wx.setStorageSync('privacy_agreed', true)
                resolve({ event: 'agree' })
              } else {
                resolve({ event: 'disagree' })
              }
            }
          })
        }
        privacyPopup()
      })
    }

  },
})
