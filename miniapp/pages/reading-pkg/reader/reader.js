// pages/reading-pkg/reader/reader.js — 音频播放器（WM6 防刷心跳链路）
// 协议（PRD R-151）：每 10 秒心跳上报；暂停/seek/退出/切倍速/结束时立即上报。
// 客户端如实上报连续段区间 [session_start, position]；seek 段由服务端区间并集排除。
const api = require('../../../utils/api')
const media = require('../../../utils/media')

const HEARTBEAT_SEC = 10
const SPEEDS = [0.75, 1.0, 1.25, 1.5, 2.0]

function fmt(sec) {
  if (!sec || sec < 0) sec = 0
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

Page({
  data: {
    book: null,
    dictWord: '',
    dictResult: null,
    dictError: '',
    showDict: false,
    dictLoading: false,
    childId: null,
    bookId: null,
    childName: '',
    playing: false,
    currentTime: 0,
    duration: 0,
    displayTime: '0:00',
    displayDuration: '0:00',
    sliderValue: 0,
    sliderMax: 0,
    speed: 1.0,
    speeds: SPEEDS,
    coveragePercent: 0,
    finished: false,
    showCompletion: false,
    completion: null,
    lastPosition: 0,
    anomalyMsg: '',
  },

  onLoad(options) {
    let book = {}
    try { book = JSON.parse(decodeURIComponent(options.book || '{}')) } catch (e) { /* ignore */ }
    // C52 同款兜底：reLaunch/分享进入只有 URL 参数，补齐 id/title
    if (!book.id && options.book_id) book.id = Number(options.book_id)
    if (!book.title && options.book_title) book.title = decodeURIComponent(options.book_title)
    book = media.formatBook(book)
    const duration = book.audio_duration || 0
    this.setData({
      book,
      bookId: book.id || null,
      childId: options.child_id ? Number(options.child_id) : null,
      childName: options.child_name ? decodeURIComponent(options.child_name) : '',
      duration,
      displayDuration: fmt(duration),
      sliderMax: Math.max(1, Math.floor(duration)),
    })
    this._sessionStart = null
    this._lastBeatAt = 0
    this._audio = null
    this._resumeApplied = false
    this._initAudio()
    this.loadProgress()
  },

  onUnload() {
    this._reportNow(true)
    if (this._audio) {
      this._audio.destroy()
      this._audio = null
    }
  },

  onHide() {
    this._reportNow(true)
  },

  async loadProgress() {
    const { bookId, childId } = this.data
    if (!childId || !bookId) return
    try {
      const p = await api.getProgress(bookId, childId)
      this.setData({
        coveragePercent: p.coverage_percent || 0,
        finished: !!p.finished,
        lastPosition: p.last_position || 0,
      })
      this._applyResume()
    } catch (e) { /* 新书无进度 */ }
  },

  _applyResume() {
    // 有历史进度则从 last_position 续播（onCanplay 与 loadProgress 双保险，防基础库不触发）
    const { lastPosition } = this.data
    if (lastPosition > 0 && this._audio && !this._resumeApplied) {
      this._audio.seek(lastPosition)
      this._resumeApplied = true
    }
  },

  _initAudio() {
    const { book } = this.data
    const token = wx.getStorageSync('token')
    const base = book.audio_url.split('?')[0]
    const url = `${base}?token=${encodeURIComponent(token)}`
    const audio = wx.createInnerAudioContext()
    audio.src = url
    audio.playbackRate = 1.0
    audio.obeyMuteSwitch = false

    audio.onCanplay(() => {
      if (this.data.duration <= 0 && audio.duration) {
        this.setData({ duration: audio.duration, displayDuration: fmt(audio.duration), sliderMax: Math.floor(audio.duration) })
      }
      this._applyResume()
    })
    audio.onPlay(() => {
      if (this._sessionStart === null) this._sessionStart = Math.floor(audio.currentTime || 0)
      this.setData({ playing: true })
    })
    audio.onPause(() => {
      this._reportNow()
      this.setData({ playing: false })
    })
    audio.onStop(() => {
      this._reportNow()
      this.setData({ playing: false })
    })
    audio.onEnded(() => {
      // 播放结束 currentTime 已归零，_reportNow 会因 position<=sessionStart 跳过，
      // 最后一段永远丢失（4 秒音频只记到 3 秒，95% 完播判定失败）——用总时长兜底
      const endPos = Math.floor(audio.duration || this.data.duration || 0)
      if (this._sessionStart !== null && endPos > this._sessionStart) {
        // E-20260830-11：上报成功后再刷新 UI，避免 loadProgress 竞态拿到旧值
        api
          .reportProgress(this.data.childId, this.data.bookId, endPos, this._sessionStart)
          .then(() => this.loadProgress())
          .catch(() => {})
        this._sessionStart = endPos
        this._lastBeatAt = Date.now()
      }
      this.setData({ playing: false })
    })
    audio.onError((err) => {
      this.setData({ playing: false, anomalyMsg: '音频加载失败，请稍后再试' })
      console.error('[audio error]', err)
    })
    audio.onTimeUpdate(() => {
      const cur = audio.currentTime || 0
      // 时长校准兜底：onCanplay 在部分基础库不触发，sliderMax 会停在初始值，滑块看似不动
      const realDur = audio.duration || 0
      if (realDur > 0 && Math.floor(realDur) !== this.data.sliderMax) {
        this.setData({ duration: realDur, displayDuration: fmt(realDur), sliderMax: Math.floor(realDur) })
      }
      this.setData({
        currentTime: cur,
        displayTime: fmt(cur),
        sliderValue: Math.floor(cur),
      })
      // F-H5/T26：onTimeUpdate 实际触发频率 ~250ms 而非 1s，原计数法上报量 ~4×；
      // 改墙钟差值判断（HEARTBEAT_SEC 秒真实间隔一次）
      if (!this._lastBeatAt) this._lastBeatAt = Date.now()
      if (Date.now() - this._lastBeatAt >= HEARTBEAT_SEC * 1000) {
        this._lastBeatAt = Date.now()
        this._reportNow()
      }
    })
    audio.onSeeked(() => {
      // seek 后重置连续段起点：旧区间已在 onSliderChanging 前上报
      this._sessionStart = Math.floor(audio.currentTime || 0)
      this._lastBeatAt = Date.now()
    })
    this._audio = audio
  },

  onTogglePlay() {
    const audio = this._audio
    if (!audio) return
    if (this.data.playing) {
      audio.pause()
    } else {
      audio.play()
    }
  },

  onSkipBack() {
    const audio = this._audio
    if (!audio) return
    this._reportNow()
    const target = Math.max(0, (audio.currentTime || 0) - 15)
    audio.seek(target)
  },

  onSliderChanging(e) {
    // 拖动中先上报当前区间（防刷：seek 前的位置区间如实入账）
    if (this._sessionStart !== null && this._audio && this.data.playing) {
      this._reportNow()
    }
    this._pendingSlider = e.detail.value
  },

  onSliderChange(e) {
    const audio = this._audio
    if (!audio) return
    audio.seek(e.detail.value)
  },

  onSpeedTap(e) {
    const rate = Number(e.currentTarget.dataset.rate)
    const audio = this._audio
    if (!audio || rate === this.data.speed) return
    this._reportNow() // 切倍速时上报（R-151 上报时机 5）
    audio.playbackRate = rate
    this.setData({ speed: rate })
  },

  async _reportNow(sync = false) {
    const audio = this._audio
    if (!audio) return
    const { book, childId } = this.data
    if (!childId || this._sessionStart === null) return
    const position = Math.floor(audio.currentTime || 0)
    const sessionStart = this._sessionStart
    if (position <= sessionStart) return
    // 上报后新会话段从当前位置继续累积
    this._sessionStart = position
    this._lastBeatAt = Date.now()
    const done = (res) => {
      if (!res) return
      this.setData({
        coveragePercent: res.coverage_percent || 0,
        finished: !!res.finished,
      })
      if (res.just_finished) {
        this.setData({
          showCompletion: true,
          completion: {
            streak: (res.checkin && res.checkin.streak) || 1,
            checkedIn: !!(res.checkin && res.checkin.checked_in),
            minutes: res.reading_minutes || Math.floor((book.audio_duration || 0) / 60),
          },
        })
        wx.vibrateShort({ type: 'medium' })
      }
    }
    const fail = (err) => {
      const msg = (err && err.message) || ''
      if (msg.indexOf('异常') !== -1) {
        this.setData({ anomalyMsg: msg })
        setTimeout(() => this.setData({ anomalyMsg: '' }), 4000)
      }
    }
    try {
      const res = await api.reportProgress(childId, this.data.bookId, position, sessionStart)
      done(res)
    } catch (e) {
      fail(e)
    }
  },

  onCloseCompletion() {
    this.setData({ showCompletion: false })
  },

  // ---------- 查词（WM8：上半屏播放不受影响） ----------
  onDictInput(e) {
    this.setData({ dictWord: e.detail.value })
  },

  async onDictSearch() {
    const w = (this.data.dictWord || '').trim()
    if (!w) {
      wx.showToast({ title: '先输入要查的单词', icon: 'none' })
      return
    }
    this.setData({ dictLoading: true, showDict: true, dictResult: null, dictError: '' })
    try {
      const r = await api.lookupWord(w, this.data.childId, this.data.bookId)
      this.setData({
        dictResult: r,
        dictError: '',
        dictLoading: false,
        showDict: true,
      })
    } catch (e) {
      this.setData({
        dictResult: null,
        dictError: (e && e.message) || '查询失败',
        dictLoading: false,
        showDict: true,
      })
    }
  },

  onToggleDict() {
    this.setData({ showDict: !this.data.showDict })
  },
})
