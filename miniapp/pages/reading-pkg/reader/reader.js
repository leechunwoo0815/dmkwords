// pages/reading-pkg/reader/reader.js — 音频播放器（WM6 防刷心跳链路）
// 协议（PRD R-151）：每 10 秒心跳上报；暂停/seek/退出/切倍速/结束时立即上报。
// 客户端如实上报连续段区间 [session_start, position]；seek 段由服务端区间并集排除。
//
// 2026-10-30 播放器重做（甲方验收退回「进度条、时长、进度显示不对」）：
//   · 时长唯一真源 = 后端 `books.audio_duration_seconds`（详情接口的 audio_duration）。
//     iOS 的 InnerAudioContext.duration 对部分 MP3 恒为 0，不能当总时长用；
//   · 三个入口（书详情/首页/书架）都只传 book_id ⇒ **进页必拉详情**，不再靠入口对象；
//   · 时长未知时显示 `--:--`，进度条只读；
//   · 状态机 idle/loading/playing/paused/ended/error，结束可重听、失败可重试；
//   · 进度条只读（2026-11-03 甲方裁定：禁用拖动与 ±15s，只保留播放/暂停/倍速），
//     避免手动 seek 制造"覆盖 81.5% 但播放头 24s"的错位；
//   · 续播点 = 已覆盖最远点（后端 resume_position；旧后端回退 max(last_position, coverage)）。
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

function percent(cur, dur) {
  const c = Number(cur) || 0
  const d = Number(dur) || 0
  if (d <= 0) return 0
  return Math.min(100, Math.round((c * 1000) / d) / 10)
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
    progressPercent: 0,
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
    this._resumeAttempts = 0
    this._resumeTarget = null
    this._reporting = false
    this._seekEpoch = 0
    this._rawCurrent = 0
    this._desiredSpeed = 1.0   // iOS 倍速真实生效点：见 _applySpeed
    this._speedDirty = false
    this._speedRetryAt = 0
    this._speedTimers = []
    this._setAudioOption()
    // 三个入口都只带 book_id ⇒ 详情必拉（音频地址/时长/书名都从详情补全）
    this.loadDetailAndInit()
    this.loadProgress()
  },

  onUnload() {
    this._reportNow(true)
    this._clearSpeedTimers()
    if (this._audio) {
      this._audio.destroy()
      this._audio = null
    }
  },

  onHide() {
    this._reportNow(true)
  },

  // 锁屏/切后台回来后系统可能把 playbackRate 打回 1.0，且不一定再触发 onPlay——
  // 回前台统一补一次（甲方真机：锁屏再打开倍速"突然生效"，之后再点又失效）。
  onShow() {
    if (this._audio) this._reapplySpeedSoon()
  },

  // iOS：全局音频选项比 InnerAudioContext 的 per-context 属性可靠；
  // 静音键打开时仍要出声（甲方实测"进度在走但没声音"即此族）。
  _setAudioOption() {
    if (!wx.setInnerAudioOption) return
    try {
      wx.setInnerAudioOption({ obeyMuteSwitch: false, mixWithOther: true, speakerOn: true })
    } catch (e) { /* 老基础库无此 API：忽略 */ }
  },

  // ---------- 倍速（iOS 专项） ----------
  // 甲方 2026-11-03 真机报障：点倍速没反应，锁屏再打开突然生效，再点其它倍速又失灵。
  // 根因：InnerAudioContext.playbackRate 在"未真正起播/暂停/系统打断"时写入会被静默
  // 忽略，只在创建 context 和点击那一下设置不够。唯一稳的做法是记住目标值，在
  // canplay/play/seeked/回前台后反复重设并读回校验。
  _setDesiredSpeed(rate) {
    const v = Number(rate)
    this._desiredSpeed = v && isFinite(v) && v > 0 ? v : 1.0
    this._speedDirty = true
    this._speedRetryAt = 0
    this._applySpeed()
  },

  _applySpeed() {
    const audio = this._audio
    if (!audio) return
    const rate = this._desiredSpeed || 1.0
    try {
      if (Number(audio.playbackRate) !== rate) audio.playbackRate = rate
      // iOS 偶发丢弃第一次写入：立刻读回，不一致再补一次
      if (Number(audio.playbackRate) !== rate) audio.playbackRate = rate
      this._speedDirty = Number(audio.playbackRate) !== rate
    } catch (e) { /* 老基础库不支持倍速：保持 1.0 */ }
  },

  /** 起播瞬间系统时序未稳，连拍三次覆盖"设置被吞"的窗口。 */
  _reapplySpeedSoon() {
    this._clearSpeedTimers()
    this._applySpeed()
    ;[120, 350, 800].forEach((ms) => {
      this._speedTimers.push(setTimeout(() => this._applySpeed(), ms))
    })
  },

  _clearSpeedTimers() {
    (this._speedTimers || []).forEach((t) => clearTimeout(t))
    this._speedTimers = []
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
    const patch = {
      duration: d,
      durationKnown: true,
      displayDuration: fmt(d),
      progressPercent: percent(this.data.currentTime, d),
    }
    this.setData(patch)
  },

  async loadProgress() {
    const { bookId, childId } = this.data
    if (!childId || !bookId) return
    try {
      const p = await api.getProgress(bookId, childId)
      // 续播点：后端 resume_position = max(last_position, 已覆盖最远点)；
      // 旧后端没有该字段时回退 max(last_position, coverage_seconds)——历史拖动只推进
      // 覆盖、不推进 last_position，按 last_position 会出现"显示 81.5%、点进去 0:24"。
      const resume = Number(
        p.resume_position != null
          ? p.resume_position
          : Math.max(p.last_position || 0, p.coverage_seconds || 0)
      ) || 0
      this.setData({
        coveragePercent: p.coverage_percent || 0,
        finished: !!p.finished,
        lastPosition: resume,
      })
      this._applyResume()
    } catch (e) { /* 新书无进度 */ }
  },

  _applyResume() {
    // 有历史进度则从 last_position 续播（onCanplay / 实例建成 / 按下播放 三处触发，防基础库时序差异）
    // R6（插修7）：已读完的书不续播——last_position≈99% 时点播放只剩几秒
    // "一闪而过伪音"，还需二次点击才从头；"再次阅读"应从头开始，续播仅服务未读完的书
    const { lastPosition, finished, duration, durationKnown } = this.data
    if (lastPosition <= 0 || finished || !this._audio || this._resumeApplied) return
    // 续播点贴尾（≤3 秒）视同读完：seek 过去会立刻 ended，体验像"点了没反应"
    if (durationKnown && lastPosition >= duration - 3) return
    // 音频未就绪时 seek 会被播放器丢弃（2026-10-30 模拟器实证：currentTime 仍从 0 走）——
    // 所以这里**不置 _resumeApplied**，由 onSeeked 确认落点后才算完成；
    // 未确认就等 canplay/onPlay 再补，最多 4 次，避免用户反复拖动时刷屏。
    if ((this._resumeAttempts || 0) >= 4) return
    this._resumeAttempts = (this._resumeAttempts || 0) + 1
    this._resumeTarget = lastPosition
    this._audio.seek(lastPosition)
    this.setData({
      currentTime: lastPosition,
      displayTime: fmt(lastPosition),
      progressPercent: percent(lastPosition, duration),
    })
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
    // v=2：2026-11-03 把 6 个 5–6 秒占位音频换成 90 秒静音——同一路径会被
    // InnerAudioContext/客户端缓存旧文件，加版本参数强制换 URL。
    const url = `${base}?child_id=${childId}&token=${encodeURIComponent(token)}&v=2`
    const audio = wx.createInnerAudioContext()
    audio.src = url
    audio.volume = 1.0
    audio.playbackRate = this._desiredSpeed || this.data.speed
    audio.obeyMuteSwitch = false

    audio.onCanplay(() => {
      const ad = Number(audio.duration)
      if (!this.data.durationKnown && ad > 0 && isFinite(ad)) this._applyDuration(ad)
      this.setData({ audioLoading: false })
      if (this.data.playState === 'loading') this.setData({ playState: 'idle' })
      this._applySpeed()
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
      // iOS：倍速必须等真正起播后再设（创建/暂停时写入常被忽略），且要连拍几次
      this._reapplySpeedSoon()
      // 起播后再补一次续播：iOS 的 canplay 常在 play 之后才到，之前那次 seek 会被丢弃
      if (!this._resumeApplied) this._applyResume()
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
        progressPercent: 100,
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
      // 兜底：系统打断/切后台回来可能静默把倍速打回 1.0 且不再触发 onPlay。
      // 1 秒最多补写一次——老基础库不支持倍速时 readback 恒不等，避免每 tick 空写。
      if (this._speedDirty && Date.now() >= (this._speedRetryAt || 0)) {
        this._speedRetryAt = Date.now() + 1000
        this._applySpeed()
      }
      // 时长校准兜底：后端没给时长时，用播放器自报时长补（部分基础库 onCanplay 不触发）
      if (!this.data.durationKnown) {
        const ad = Number(audio.duration)
        if (ad > 0 && isFinite(ad)) this._applyDuration(ad)
      }
      const patch = {
        currentTime: cur,
        displayTime: fmt(cur),
        progressPercent: percent(cur, this.data.duration),
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
      // seek 后重置连续段起点（拖动已禁用，这里只剩续播/重听的内部 seek）
      const cur = audio.currentTime || 0
      // 续播只有**真的落在 last_position 附近**才算完成——音频未就绪时 seek 会被丢弃，
      // 不能凭"调用过 seek"就认账（否则 canplay/onPlay 的补 seek 全被跳过，用户永远从头听）
      if (!this._resumeApplied && this._resumeTarget != null && Math.abs(cur - this._resumeTarget) <= 2) {
        this._resumeApplied = true
        this._resumeTarget = null
      }
      this._seekEpoch += 1
      this._sessionStart = Math.floor(cur)
      this._lastBeatAt = Date.now()
      // 部分基础库 seek 后把 playbackRate 重置为 1.0（续播/重听都会 seek）
      this._applySpeed()
      this.setData({
        currentTime: cur,
        displayTime: fmt(cur),
        progressPercent: percent(cur, this.data.duration),
      })
    })
    this._audio = audio
    // 进度可能比音频实例先到（进度接口与详情接口竞态）：实例一建好就补一次续播。
    // 只有 canplay 才续播是不够的——iOS 上 canplay 往往要等用户按下播放才触发，
    // 那一下已经从头出声了（甲方报障「进去点播放还是从头」的根因）。
    this._applyResume()
  },

  /** 内部定位（仅重听）：勾掉自动续播，别让补 seek 把人拖回去。 */
  _userSeek() {
    this._resumeApplied = true
    this._resumeTarget = null
    this._seekEpoch += 1
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
      this._userSeek()
      audio.seek(0)
      this._sessionStart = 0
      this.setData({ currentTime: 0, displayTime: '0:00', progressPercent: 0 })
    } else if (!this._resumeApplied) {
      // 起播前最后一道续播闸：用户手速快于 canplay/进度回调时，先 seek 再 play
      this._applyResume()
    }
    audio.play()
  },

  onRetry() {
    this.setData({ anomalyMsg: '', canRetry: false, playState: 'idle', audioLoading: true })
    this.loadDetailAndInit()
  },

  onSpeedTap(e) {
    const rate = Number(e.currentTarget.dataset.rate)
    if (!rate || rate === this.data.speed) return
    this._reportNow() // 切倍速时上报（R-151 上报时机 5）
    this.setData({ speed: rate })
    this._setDesiredSpeed(rate)
    // 暂停时点倍速：只记目标值（iOS 暂停态写入会被忽略），等下一次 onPlay 落地
    if (this.data.playing) this._reapplySpeedSoon()
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
