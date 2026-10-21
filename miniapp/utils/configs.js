// miniapp/utils/configs.js — 业务口径唯一来源（后端下发，第八轮审查 E 批剩余）
//
// 为什么要这一层：小程序原先有 13 处硬编码业务数字（预约保留 72 小时、完播阈值 95%、
// 观察期 30 天、续借 1 次、短信重发 60 秒…），与配置中心各写一份 —— 运营改配置后文案
// 不跟随；P2-73 更是三方不一致（服务端 90 秒 vs 小程序 60 秒）：家长 60 秒后点发送必被拒。
// 现在：**数字只从后端来**（GET /api/miniapp/configs，公开端点），本模块负责取一次 + 缓存。
//
// 兜底（FALLBACK）与 `backend/seeds/seed_configs.py` 的默认值一致：**只在请求失败时使用**，
// 目的是"后端暂时取不到也不至于把页面文案变成空白"——它不是第二套口径，正常路径永远走服务端值。
// 取数失败时 `loadConfigs()` 返回 `failed: true`，**资金/时限类**文案（预约成功、退款时限）
// 会追加一句如实提示，其余展示文案静默用兜底（与修复前行为相同，不会更差）。
const api = require('./api')

const FALLBACK = {
  reservation_hours: 72,
  audio_finish_threshold_percent: 95,
  observation_period_days: 30,
  transfer_review_timeout_hours: 72,
  renew_limit: 1,
  renew_days: 7,
  sms_send_interval_seconds: 90,
  quiz_questions_per_book: 5,
  quiz_max_attempts: 3,
  activity_refund_cutoff_hours: 2,
}

let _configs = null
let _failed = false
let _inflight = null

// 取一次并缓存（同一会话内不重复请求；force=true 可强制刷新）
function loadConfigs(force) {
  if (!force && _configs) return Promise.resolve({ configs: _configs, failed: _failed })
  if (!force && _inflight) return _inflight
  _inflight = api
    .getConfigs()
    .then((r) => {
      _configs = Object.assign({}, FALLBACK, r || {})
      _failed = false
      return { configs: _configs, failed: false }
    })
    .catch((e) => {
      // 取不到：用兜底值让页面能渲染，并如实标记 failed（调用方决定要不要提示）
      console.warn('[configs] 口径下发失败，暂用兜底值', e)
      _configs = Object.assign({}, FALLBACK)
      _failed = true
      return { configs: _configs, failed: true }
    })
    .then((r) => {
      _inflight = null
      return r
    })
  return _inflight
}

// 同步读（页面渲染用；未加载过则返回兜底——正常流程都会先 await loadConfigs()）
function getConfigs() {
  return _configs || Object.assign({}, FALLBACK)
}

function configsFailed() {
  return _failed
}

module.exports = { loadConfigs, getConfigs, configsFailed, FALLBACK }
