// frontend/utils/consent.js — 三段式监护人同意（前端流程唯一入口）
//
// 用法：
//   const consent = require('../../utils/consent')
//   const ok = await consent.ensure('child_data')   // 已有同意或用户刚同意 → true
//   consent.ensureForError(err, 'child_data')        // 后端 403 errorCode 匹配时弹窗补同意

const request = require('./request')

const MODAL_TITLE = {
  privacy_policy: '隐私政策',
  child_data: '儿童信息收集同意',
  voice_recording: '语音数据收集同意',
}

const TYPE_TO_ERROR_CODE = {
  privacy_policy: 'consent_required',
  child_data: 'consent_required',
  voice_recording: 'voice_consent_required',
}

// 兜底摘要（网络失败或文案过长时使用，与后端 consent_texts.py 同源）
const FALLBACK_SUMMARY = {
  privacy_policy: '请阅读并同意《隐私政策》后继续使用。完整文本见登录页"隐私政策"链接。',
  child_data:
    '我们将收集孩子的姓名、年龄、年级及阅读记录、测评成绩，' +
    '仅用于分级阅读推荐、阅读报告生成与晋级评定。您可随时撤回同意并删除数据。',
  voice_recording:
    '朗读功能需要录制孩子的语音，用于朗读打卡与发音评估。' +
    '录音文件安全存储，仅您和指导老师可查看，保留6个月后自动删除。',
}

let _textsCache = null

function getConsentTexts() {
  if (_textsCache) return Promise.resolve(_textsCache)
  return request
    .get('/user/consent/texts', null, { auth: false, showError: false })
    .then(res => {
      _textsCache = res
      return res
    })
    .catch(() => ({ version: '', texts: {} }))
}

function hasValidConsent(type) {
  return Promise.all([request.get('/user/consent', null, { showError: false }), getConsentTexts()])
    .then(([res, textsRes]) => {
      const list = (res && res.consents) || []
      const currentVersion = textsRes.version
      return list.some(
        c =>
          c.consent_type === type &&
          !c.withdrawn_at &&
          // 版本升级后旧同意失效，需重新征求（F3）
          (!currentVersion || c.consent_version === currentVersion)
      )
    })
    .catch(() => false) // 查询失败按无同意处理，走弹窗引导
}

function grant(type) {
  return request.post('/user/consent', { consent_type: type })
}

// 主入口：确保某类同意存在；resolve(true)=已有或刚同意，resolve(false)=用户拒绝/异常
function ensure(type) {
  return hasValidConsent(type).then(exists => {
    if (exists) return true
    return getConsentTexts().then(textsRes => {
      const full = textsRes.texts && textsRes.texts[type]
      // wx.showModal content 不可滚动，超过 180 字用摘要
      const content =
        full && full.length <= 180 ? full : FALLBACK_SUMMARY[type] || '请同意后继续'
      return new Promise(resolve => {
        wx.showModal({
          title: MODAL_TITLE[type] || '监护人同意',
          content,
          confirmText: '同意并继续',
          cancelText: '不同意',
          success(m) {
            if (!m.confirm) {
              resolve(false)
              return
            }
            grant(type)
              .then(() => resolve(true))
              .catch(() => {
                wx.showToast({ title: '网络异常，请重试', icon: 'none' })
                resolve(false)
              })
          },
          fail() {
            resolve(false)
          },
        })
      })
    })
  })
}

// 处理后端 403：err.errorCode 与该类型匹配时弹窗请求同意；resolve(true)=用户已同意
function ensureForError(err, type) {
  if (err && err.errorCode === TYPE_TO_ERROR_CODE[type]) return ensure(type)
  return Promise.resolve(false)
}

// 每次启动最多提示一次（防用户拒绝后每次 onShow 重复打扰）
const _promptedThisSession = new Set()

function ensureOnce(type) {
  if (_promptedThisSession.has(type)) return Promise.resolve(false)
  _promptedThisSession.add(type)
  return ensure(type)
}

module.exports = { ensure, ensureOnce, ensureForError, hasValidConsent }
