// pages/reading-pkg/reader/reader.js — 音频播放器（WM6 防刷心跳链路）
// 协议（PRD R-151）：每 10 秒心跳上报；暂停/seek/退出/切倍速/结束时立即上报。
// 客户端如实上报连续段区间 [session_start, position]；seek 段由服务端区间并集排除。
//
// 2026-10-30 播放器重做（甲方验收退回「进度条、时长、进度显示不对」）：
//   · 时长唯一真源 = 后端 `books.audio_duration_seconds`（详情接口的 audio_duration）。
//     iOS 的 InnerAudioContext.duration 对部分 MP3 恒为 0，不能当总时长用；
//   · 三个入口（书详情/首页/书架）都只传 book_id ⇒ **进页必拉详情**，不再靠入口对象；
//   · 时长未知时显示 `--:--` 且滑块禁用——不再用 `0:00 + max=1` 假装"拖到最右"；
//   · 状态机 idle/loading/playing/paused/ended/error，结束可重听、失败可重试；
//   · 拖动期间不被 250ms 的 onTimeUpdate 顶回去。
const api = require('../../../utils/api')
const media = require('../../../utils/media')
const { loadConfigs, getConfigs } = require('../../../utils/configs')
const session = require('../../../utils/session')

const HEARTBEAT_SEC = 10
const SPEEDS = [0.75, 1.0, 1.25, 1.5, 2.0]

function fmt(sec) {
  const n = Number(sec)
  if (!n || !isFinite(n) || n < 0) return '0:00'
  const total = Math.floor(n)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

Page({
  data: {
    cfg: getConfigs(),  // 业务口径（后端下发；见 utils/configs.js）
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
    playState: 'idle',   // idle | loading | playing | paused | ended | error
    currentTime: 0,
    duration: 0,
    durationKnown: false,
    displayTime: '0:00',
    displayDuration: '--:--',
    sliderValue: 0,
    sliderMax: 1,
    speed: 1.0,
    speeds: SPEEDS,
    coveragePercent: 0,
    finished: false,
    showCompletion: false,
    completion: null,
    lastPosition: 0,
    anomalyMsg: '',
    audioLoading: false,
    canRetry: false,
  },

  async onLoad(options) {
    const { configs } = await loadConfigs()
    this.setData({ cfg: configs })
    let book = {}
    try { book = JSON.parse(decodeURIComponent(options.book || '{}')) } catch (e) { /* ignore */ }
    // C52 同款兜底：reLaunch/分享进入只有 URL 参数，补齐 id/title
    if (!book.id && options.book_id) book.id = Number(options.book_id)
    // P1-6（第八轮大审查）：兼容"借阅行形态"的对象（字段是 book_id 而非 id）——
    // 两个入口（书架/首页）已改传 id，这里兜底防再次传入对象时静默无 id。
    if (!book.id && book.book_id) book.id = Number(book.book_id)
    if (!book.title && options.book_title) book.title = decodeURIComponent(options.book_title)
    // 入口若带了时长（book-detail 会带），先立即可见；真源仍以详情接口为准
    if (!book.audio_duration && options.audio_duration) {
      book.audio_duration = Number(options.audio_duration) || 0
    }
    book = media.formatBook(book)
    this.setData({
      book,
      bookId: book.id || null,
      childId: session.resolveChildId(options),
      childName: options.child_name ? decodeURIComponent(options.child_name) : '',
    })
    this._applyDuration(book.audio_duration)
    this._sessionStart = null
    this._lastBeatAt = 0
    this._finishPrompted = false // B1：同一次会话只弹一次完播引导（onLoad 重置）
    this._audio = null
    this._resumeApplied = false
    this._seeking = false
    this._reporting = false
    this._seekEpoch = 0
    this._rawCurrent = 0
    this._setAudioOption()
    // 三个入口都只带 book_id ⇒ 详情必拉（音频地址/时长/书名都从详情补全）
    this.loadDetailAndInit()
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

  // iOS：全局音频选项比 InnerAudioContext 的 per-context 属性可靠；
  // 静音键打开时仍要出声（甲方实测"进度在走但没声音"即此族）。
  _setAudioOption() {
    if (!wx.setInnerAudioOption) return
    try {
      wx.setInnerAudioOption({ obeyMuteSwitch: false, mixWithOther: true, speakerOn: true })
    } catch (e) { /* 老基础库无此 API：忽略 */ }
  },

  async loadDetailAndInit() {
    let fresh = null
    if (this.data.bookId) {
      try {
        fresh = await api.getBookDetail(this.data.bookId)
      } catch (e) {
        // 详情失败：若入口自带可播信息仍可起播；否则给可见失败态 + 重试
        if (!this.data.book || !this.data.book.audio_url) {
          this.setData({
            playState: 'error',
            canRetry: true,
            anomalyMsg: (e && e.message) || '书籍信息加载失败，请重试',
          })
          return
        }
      }
    }
    if (fresh) {
      const merged = media.formatBook({ ...this.data.book, ...fresh })
      this.setData({ book: merged })
      this._applyDuration(merged.audio_duration)
    }
    this._initAudio()
  },

  /** 总时长落地（唯一写入口）：后端秒数优先，audio.duration 只在后端缺值时兜底。 */
  _applyDuration(sec) {
    const d = Number(sec)
    if (!d || !isFinite(d) || d <= 0) return
    if (this.data.durationKnown && Math.abs(d - this.data.duration) < 0.5) return
    const max = Math.max(1, Math.floor(d))
    const patch = {
      duration: d,
      durationKnown: true,
      displayDuration: fmt(d),
      sliderMax: max,
      sliderValue: Math.min(this.data.sliderValue, max),
    }
    this.setData(patch)
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
    // R6（插修7）：已读完的书不续播——last_position≈99% 时点播放只剩几秒
    // "一闪而过伪音"，还需二次点击才从头；"再次阅读"应从头开始，续播仅服务未读完的书
    const { lastPosition, finished, duration, durationKnown } = this.data
    if (lastPosition <= 0 || finished || !this._audio || this._resumeApplied) return
    // 续播点贴尾（≤3 秒）视同读完：seek 过去会立刻 ended，体验像"点了没反应"
    if (durationKnown && lastPosition >= duration - 3) return
    this._audio.seek(lastPosition)
    this._resumeApplied = true
    this.setData({ sliderValue: Math.floor(lastPosition), displayTime: fmt(lastPosition) })
  },

  _initAudio() {
    const { book, childId } = this.data
    const token = wx.getStorageSync('token')
    // E-20260909-02：防重复实例——二次 init 前先销毁旧 context（防双实例双上报）
    if (this._audio) {
      this._audio.destroy()
      this._audio = null
    }
    if (!book || !book.audio_url) {
      this.setData({
        playState: 'error',
        canRetry: true,
        anomalyMsg: '这本书暂时没有可播放的音频，请联系馆员',
      })
      return
    }
    this.setData({ audioLoading: true, playState: 'loading', anomalyMsg: '', canRetry: false })
    const base = book.audio_url.split('?')[0]
    // E-20260909-02（媒体消费点断链族 §六十二同族新案）：C-13 收口后音频流
    // child_id 必填（会员门禁上下文）——漏拼即全量 403「缺少 child_id」，
    // 插修 15 后端加必填参数时未枚举到此消费端，音频自该批起全量哑火
    const url = `${base}?child_id=${childId}&token=${encodeURIComponent(token)}`
    const audio = wx.createInnerAudioContext()
    audio.src = url
    audio.volume = 1.0
    audio.playbackRate = this.data.speed
    audio.obeyMuteSwitch = false

    audio.onCanplay(() => {
      const ad = Number(audio.duration)
      if (!this.data.durationKnown && ad > 0 && isFinite(ad)) this._applyDuration(ad)
      this.setData({ audioLoading: false })
      if (this.data.playState === 'loading') this.setData({ playState: 'idle' })
      this._applyResume()
    })
    audio.onWaiting(() => {
      // 只有"用户想听"时才算加载中；暂停时的缓冲不打断 UI
      if (this.data.playState === 'playing' || this.data.playState === 'loading') {
        this.setData({ audioLoading: true })
      }
    })
    audio.onPlay(() => {
      // R5（插修7）：无条件重设——ended 后再 play 微信从头播（currentTime=0），
      // 若沿用 onEnded 尾部推进的 _sessionStart=旧endPos，则再播完
      // endPos>sessionStart 恒 false，上报+测验引导弹窗链整体跳过（再读不弹根因）
      this._sessionStart = Math.floor(audio.currentTime || 0)
      this.setData({ playing: true, playState: 'playing', audioLoading: false, canRetry: false })
    })
    audio.onPause(() => {
      this._reportNow()
      this.setData({ playing: false, playState: 'paused' })
    })
    audio.onStop(() => {
      this._reportNow()
      this.setData({ playing: false, playState: 'paused' })
    })
    audio.onEnded(() => {
      // 播放结束 currentTime 已归零，_reportNow 会因 position<=sessionStart 跳过，
      // 最后一段永远丢失（4 秒音频只记到 3 秒，95% 完播判定失败）——用总时长兜底
      const total = this.data.duration || Number(audio.duration) || 0
      const endPos = Math.floor(total || audio.currentTime || 0)
      this.setData({
        playing: false,
        playState: 'ended',
        audioLoading: false,
        currentTime: total,
        displayTime: fmt(total),
        sliderValue: this.data.sliderMax,
      })
      if (this._sessionStart !== null && endPos > this._sessionStart) {
        // E-20260830-11：上报成功后再刷新 UI，避免 loadProgress 竞态拿到旧值
        api
          .reportProgress(this.data.childId, this.data.bookId, endPos, this._sessionStart)
          .then(() => this.loadProgress())
          .then(async () => {
            // B1（插修5）：完播即时引导测验——以 loadProgress 后的 finished 为准
            //（心跳跨阈值时 finished 已置 1，onEnded 只是常见触发点之一；
            // report 响应的 just_finished 只在跃迁那次返回，不消费它以覆盖全路径）
            if (!this.data.finished || this._finishPrompted) return
            // R2（插修6）测验状态门控（用户裁定 2026-09-04）："测验没通过、次数
            // 小于 3 次，读完就应该弹提示引导"。后端语义实测（growth/service.py
            // get_quiz）：locked=未读完 / passed=已通过（WordsLedger 入账）/
            // failed=次数已用尽 / available=可测——仅 available 弹；
            // passed 不弹（孩子已过关）、failed 不弹（无意义）
            try {
              const q = await api.getQuiz(this.data.bookId, this.data.childId)
              if (q.status !== 'available') return
              this._finishPrompted = true // 会话内防重复轰炸（跨会话 onLoad 重置，可再弹）
              // U3（插修8）：文案与详情页徽章语气统一（儿童激励口径）
              // WM14-A（Q5 裁决）：完读弹窗主职是测验转化（词数入账核心漏斗）——
              // 保持两键「去测验/先不」，不塞晒成就（wx.showModal 仅两键，塞进去
              // 会与去测验互斥且吃掉"稍后再说"语义）。晒卡入口 = quiz-result 结果页
              // 按钮 + 阅读护照「我的成就」+ 阅读圈页顶
              wx.showModal({
                title: '已读完！',
                content: `太棒了，读完啦！要不要挑战一下阅读小挑战？还剩 ${q.attempts_left} 次机会`,
                confirmText: '去挑战',
                cancelText: '先不',
                success: (r) => {
                  if (r.confirm) {
                    // R1（插修6）：补 child_id/child_name——原 URL 漏参导致
                    // quiz 页 Number(options.child_id)=NaN → 接口 422
                    wx.navigateTo({
                      url:
                        `/pages/reading-pkg/quiz/quiz?book_id=${this.data.bookId}` +
                        `&book_title=${encodeURIComponent(this.data.book.title || '')}` +
                        `&child_id=${this.data.childId}` +
                        `&child_name=${encodeURIComponent(this.data.childName || '')}`,
                    })
                  }
                },
              })
            } catch (e) { /* getQuiz 失败不阻塞阅读体验 */ }
          })
          .catch(() => {})
        this._sessionStart = endPos
        this._lastBeatAt = Date.now()
      }
    })
    audio.onError((err) => {
      // R3（插修 16）：403（无收听权限）与真加载失败区分提示——预检端点
      // 是主防线（book-detail onPlay），此处兜底直链进入场景。
      // 2026-10-29：把微信错误码显示出来，方便定位"能请求 200 但播放器不响"。
      const code = err && err.errCode !== undefined ? err.errCode : ''
      this.setData({
        playing: false,
        playState: 'error',
        audioLoading: false,
        canRetry: true,
        anomalyMsg: `无法播放${code !== '' ? `（错误码 ${code}）` : ''}，请点重试或返回`,
      })
      console.error('[audio error]', err)
    })
    audio.onTimeUpdate(() => {
      const cur = audio.currentTime || 0
      this._rawCurrent = cur
      // 时长校准兜底：后端没给时长时，用播放器自报时长补（部分基础库 onCanplay 不触发）
      if (!this.data.durationKnown) {
        const ad = Number(audio.duration)
        if (ad > 0 && isFinite(ad)) this._applyDuration(ad)
      }
      const patch = { currentTime: cur, displayTime: fmt(cur) }
      // 拖动中不回写滑块（否则 250ms 一次的时间回调会把拇指顶回去）
      if (!this._seeking) {
        patch.sliderValue = Math.min(Math.floor(cur), Math.max(0, this.data.sliderMax))
      }
      // F-L12/T34：高频路径（~250ms）多次 setData 合并为一次
      this.setData(patch)
      // F-H5/T26：onTimeUpdate 实际触发频率 ~250ms 而非 1s，原计数法上报量 ~4×；
      // 改墙钟差值判断（HEARTBEAT_SEC 秒真实间隔一次）
      if (!this._lastBeatAt) this._lastBeatAt = Date.now()
      if (Date.now() - this._lastBeatAt >= HEARTBEAT_SEC * 1000) {
        this._lastBeatAt = Date.now()
        this._reportNow()
      }
    })
    audio.onSeeked(() => {
      // seek 后重置连续段起点：旧区间已在 onSliderChange 前上报
      this._seekEpoch += 1
      this._sessionStart = Math.floor(audio.currentTime || 0)
      this._lastBeatAt = Date.now()
    })
    this._audio = audio
  },

  onTogglePlay() {
    const audio = this._audio
    // 详情失败/音频缺失时：点播放键 = 重试整条链路
    if (!audio) {
      this.loadDetailAndInit()
      return
    }
    if (this.data.playing) {
      audio.pause()
      return
    }
    // 播完再点 = 从头重听（微信 ended 后 play 本身会回 0，这里显式对齐 UI）
    if (this.data.playState === 'ended') {
      audio.seek(0)
      this._sessionStart = 0
      this.setData({ currentTime: 0, displayTime: '0:00', sliderValue: 0 })
    }
    audio.play()
  },

  onRetry() {
    this.setData({ anomalyMsg: '', canRetry: false, playState: 'idle', audioLoading: true })
    this.loadDetailAndInit()
  },

  onSkipBack() {
    const audio = this._audio
    if (!audio) return
    this._reportNow()
    const target = Math.max(0, (audio.currentTime || 0) - 15)
    this._seekEpoch += 1
    audio.seek(target)
    this.setData({ currentTime: target, displayTime: fmt(target), sliderValue: Math.floor(target) })
  },

  // W7-07（第十轮 P2，2026-10-24）：右侧「15s ↻」原先**没有 bindtap**——wxml 里只是个带
  // `.ctrl-skip`（含 `:active` 按压高亮）的 view → 孩子按下去有反馈却没反应，反复点。
  // 现在与左侧同源实现快进（seek 前先上报区间，口径与 onSkipBack / 拖动一致）。
  onSkipForward() {
    const audio = this._audio
    if (!audio) return
    this._reportNow()
    const dur = this.data.durationKnown ? this.data.duration : (audio.duration || 0)
    const target = dur > 0
      ? Math.min(dur, (audio.currentTime || 0) + 15)
      : (audio.currentTime || 0) + 15
    this._seekEpoch += 1
    audio.seek(target)
    this.setData({ currentTime: target, displayTime: fmt(target), sliderValue: Math.floor(target) })
  },

  onSliderChanging(e) {
    // 拖动中只更新文字：滑块由原生组件跟手，回写 value 会抖动。
    // 防刷：seek 前的位置区间先如实入账（仅播放中）。
    this._seeking = true
    if (this._sessionStart !== null && this._audio && this.data.playing) {
      this._reportNow()
    }
    const v = Number(e.detail.value) || 0
    this.setData({ displayTime: fmt(v), currentTime: v })
    this._pendingSlider = v
  },

  onSliderChange(e) {
    const audio = this._audio
    this._seeking = false
    const v = Math.max(0, Number(e.detail.value) || 0)
    const target = this.data.durationKnown ? Math.min(v, this.data.duration) : v
    this.setData({ currentTime: target, displayTime: fmt(target), sliderValue: Math.floor(target) })
    if (!audio) return
    this._seekEpoch += 1
    audio.seek(target)
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
    const { childId } = this.data
    if (!childId || this._sessionStart === null) return
    const position = Math.floor(audio.currentTime || 0)
    const sessionStart = this._sessionStart
    if (position <= sessionStart) return
    // 拖动会连续触发上报：同刻只允许一拍在途（sync = 退出/暂停/切倍速，必须如实上报，不受限）
    if (this._reporting && !sync) return
    this._reporting = true
    // R11-d/M2：会话起点只有**上报成功**后才推进（原先 await 前就推进——
    // 网络失败/5xx 时这一段永久不计，离线收听整段作废）。失败时保留旧起点，
    // 下一次心跳会把"失败区间 + 新播放区间"一起上报。
    this._lastBeatAt = Date.now()
    // 在途期间用户 seek 过：服务端已收下旧区间，但起点必须留在 seek 后的位置，
    // 否则旧位置会覆盖 onSeeked 刚设好的起点 → 跳过的区间被算成已覆盖（防刷口径）。
    const epoch = this._seekEpoch
    const done = (res) => {
      this._reporting = false
      if (epoch === this._seekEpoch) this._sessionStart = position
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
            minutes: res.reading_minutes || Math.floor((this.data.duration || 0) / 60),
          },
        })
        wx.vibrateShort({ type: 'medium' })
      }
    }
    const fail = (err) => {
      this._reporting = false
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
