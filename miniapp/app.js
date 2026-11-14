// frontend/app.js
// 接口地址（2026-11-12 微信审核驳回整改，见 docs/34 §九）：**真机一律生产域名**。
//
// 改前的坑：`envVersion === 'develop'` 时用内网 `https://192.168.18.162:8443`。
// 提审版本还没发布，审核环境被判成 develop → 审核员手机上**所有**请求打向一个内网 IP，
// 100% 连接失败 → `utils/request.js` 的 fail() 统一弹「网络异常，请检查网络连接」，
// 微信据此判「页面无法加载/无法体验核心功能」。服务端一条请求都收不到（日志可证）。
//
// 现在的判定只看**平台**，不再看版本环境：
//   - 模拟器（platform=devtools）：本地后端 http://127.0.0.1:8002（image 组件不信任自签 https）
//   - 真机：默认生产 https://saisenyoo.cn
//   - 真机要连本地后端：控制台执行 wx.setStorageSync('dev_base_url', 'https://<IP>:8443')
//     显式覆盖（P2-9 口径保留；证书 certs/server.crt，手机需安装并完全信任 ca.crt）
const PROD_BASE_URL = 'https://saisenyoo.cn'
const LOCAL_BASE_URL = 'http://127.0.0.1:8002'
const _devOverride = wx.getStorageSync('dev_base_url') || ''
const _isDevtools = (wx.getSystemInfoSync() || {}).platform === 'devtools'
const BASE_URL = _devOverride || (_isDevtools ? LOCAL_BASE_URL : PROD_BASE_URL)
// 排查用（2026-11-12）：真机调试器/vConsole 里一眼能看到这次到底打的哪个地址
console.log('[app] baseURL =', BASE_URL)

const session = require('./utils/session')

App({
  globalData: {
    baseURL: BASE_URL,
    token: '',
    userInfo: null,
    currentChild: null,
    // 隐私弹窗最近一次是否被拒绝（登录页据此显示说明区与出口，见 login.js）
    privacyRefused: false,
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
            content: '使用 DmkWords 前请阅读并同意《隐私政策》。我们仅收集：您的手机号（登录与到店核验）、孩子的姓名/昵称、内置头像选择、年龄与年级（阅读服务与适读内容）、阅读记录。不收集位置、通讯录、相册或微信昵称头像。全文见「我的 → 隐私政策」。',
            confirmText: '同意',
            cancelText: '拒绝',
            success: (res) => {
              if (res.confirm) {
                // B8（2026-10-28）：本地同意留痕补**版本 + 时间戳**（后端 consent 表属 FEAT-006，
                // 尚未实现）；至少让"同意的是哪一版、什么时候同意的"可追溯。
                // 2026-11-06：隐私政策文案按实际收集范围修订（不再声明微信昵称/头像/相册），版本升 v2。
                wx.setStorageSync('privacy_agreed', true)
                wx.setStorageSync('privacy_agreed_version', 'v2')
                wx.setStorageSync('privacy_agreed_at', Date.now())
                getApp().globalData.privacyRefused = false
                resolve({ event: 'agree' })
              } else {
                // 2026-11-12（审核驳回「点击取消/返回无效」）：拒绝后**记录下来**，
                // 登录页据此显示固定的说明区 + 三个出口（重试/体验一下/退出），
                // 而不是让按钮反复弹同一个弹窗、页面上无处可去。
                getApp().globalData.privacyRefused = true
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
