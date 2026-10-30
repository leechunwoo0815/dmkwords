// scripts/miniapp_selftest.js — 小程序工具层的**行为自检**（P3 第一百一十六批）
//
// 为什么要有它：小程序侧没有测试框架，历史上只能靠"检查器 + node --check（仅语法）"兜底——
// 但本批改动里有两处是**纯行为逻辑**，语法/命名型检查器证明不了：
//   ① `utils/request.js` 的 `withHeaders`：响应头必须真的能传到调用方（列表截断信号
//      `X-List-Truncated` 的唯一读取口，静默丢掉 = 截断又变回"看起来是全部"）；
//   ② `utils/img.js` 的 `onImgError`：必须**只清空 data-img-path 指的那个字段**，
//      没有该属性时不许乱 setData（兜底本身再抛错会把真因埋掉）。
// 这里用 `wx`/`getApp` 桩件把两个模块在 Node 里真跑一遍，断言返回值与 setData 调用。
//
// 运行：`node scripts/miniapp_selftest.js`（由 `tests/unit/test_audit8_p3_miniapp_20261022.py` 调用，
// 因此随 pytest/门禁一起跑；退出码 0 = 全绿）。

const path = require('path')

const failures = []
function check(name, cond, detail) {
  if (cond) {
    console.log(`  ✓ ${name}`)
  } else {
    failures.push(`${name}${detail ? ` — ${detail}` : ''}`)
    console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`)
  }
}

// ---------- 桩件：只实现被测路径用到的那几个 API ----------
let lastRequest = null
global.wx = {
  getStorageSync: () => 'stub-token',
  removeStorageSync: () => {},
  getNetworkType: ({ success }) => success({ networkType: 'wifi' }),
  showLoading: () => {},
  hideLoading: () => {},
  showToast: () => {},
  reLaunch: () => {},
  request: (opts) => {
    lastRequest = opts
    // 服务端返回：裸数组 + 截断信号响应头（与后端 `stamp_list_meta` 同形状）
    opts.success({
      statusCode: 200,
      data: [{ id: 1 }],
      headers: { 'x-list-limit': '50', 'X-List-Truncated': 'true' },
    })
  },
}
global.getApp = () => ({ globalData: { baseURL: 'https://api.test', token: 'stub-token' } })
global.getCurrentPages = () => []

const req = require(path.join(__dirname, '..', 'miniapp', 'utils', 'request.js'))
const img = require(path.join(__dirname, '..', 'miniapp', 'utils', 'img.js'))

;(async () => {
  console.log('miniapp 工具层行为自检')

  // ① listMeta：头名大小写不敏感 + 缺头时按"未知"（不谎报"没有更多"）
  const meta = req.listMeta({ 'x-list-limit': '50', 'X-List-Truncated': 'true' })
  check('listMeta 读大写 X-List-Truncated', meta.truncated === true, JSON.stringify(meta))
  check('listMeta 读小写 x-list-limit', meta.limit === 50, JSON.stringify(meta))
  const empty = req.listMeta(undefined)
  check('listMeta 缺头 → truncated=false / limit=0', empty.truncated === false && empty.limit === 0)

  // ② withHeaders：拿到 {data, headers}；不传时形状不变（既有 200+ 处调用零影响）
  const withH = await req.get('/api/miniapp/orders', null, { withHeaders: true })
  check('withHeaders=true → {data, headers, statusCode}',
    Array.isArray(withH.data) && withH.data.length === 1 && !!withH.headers,
    JSON.stringify(withH))
  check('withHeaders=true → headers 能读到截断信号',
    req.listMeta(withH.headers).truncated === true)
  const plain = await req.get('/api/miniapp/orders', null, {})
  check('withHeaders 缺省 → 仍是裸 data（无形状破坏）', Array.isArray(plain) && plain.length === 1,
    JSON.stringify(plain))
  check('请求真的发出去了（Authorization 带上）',
    !!lastRequest && lastRequest.header.Authorization === 'Bearer stub-token')

  // ③ img.onImgError：只清空 data-img-path 指的那个字段
  const calls = []
  const fakePage = { setData: (o) => calls.push(o) }
  img.onImgError.call(fakePage, {
    currentTarget: { dataset: { imgPath: 'books[2].cover_url' } },
  })
  check('onImgError 清空数组元素字段',
    calls.length === 1 && calls[0]['books[2].cover_url'] === '',
    JSON.stringify(calls))
  const calls2 = []
  img.onImgError.call({ setData: (o) => calls2.push(o) }, { currentTarget: { dataset: {} } })
  check('onImgError 无 data-img-path → 不 setData（兜底不许自己抛错）', calls2.length === 0)
  const calls3 = []
  img.onImgError.call({ setData: (o) => calls3.push(o) }, {})
  check('onImgError 空事件对象 → 不抛异常且不 setData', calls3.length === 0)

  // ④ reader 播放器：时长真源 / 进度条边界 / 拖动不被心跳顶回 / 结束重置
  // 2026-10-30 甲方退回「进度条、时长、进度显示不对」——原逻辑时长只认入口对象
  // （三个入口都只传 book_id ⇒ 恒为 0:00，滑块 max=1 卡最右）。这里用桩件真跑 page.onLoad。
  const apiPath = path.join(__dirname, '..', 'miniapp', 'utils', 'api.js')
  const api = require(apiPath)
  api.getConfigs = async () => ({})
  api.getProgress = async () => ({ coverage_percent: 0, finished: false, last_position: 0 })
  api.reportProgress = async () => ({ coverage_percent: 0, finished: false })
  const detail = { id: 31, title: 'Book 31', has_audio: true }
  api.getBookDetail = async () => ({ ...detail })

  let lastAudio = null
  function makeAudio() {
    const handlers = {}
    const a = {
      currentTime: 0, duration: 0, playbackRate: 1, volume: 1, obeyMuteSwitch: true,
      seekCalls: [], playCalls: 0, destroyed: false,
      onCanplay: (f) => { handlers.canplay = f },
      onWaiting: (f) => { handlers.waiting = f },
      onPlay: (f) => { handlers.play = f },
      onPause: (f) => { handlers.pause = f },
      onStop: (f) => { handlers.stop = f },
      onEnded: (f) => { handlers.ended = f },
      onError: (f) => { handlers.error = f },
      onTimeUpdate: (f) => { handlers.timeupdate = f },
      onSeeked: (f) => { handlers.seeked = f },
      play: () => { a.playCalls += 1 },
      pause: () => {},
      seek: (t) => { a.seekCalls.push(t); if (a.dropSeek) return; a.currentTime = t; if (handlers.seeked) handlers.seeked() },
      destroy: () => { a.destroyed = true },
      emit: (name) => handlers[name] && handlers[name](),
    }
    return a
  }
  global.wx.setInnerAudioOption = () => {}
  global.wx.createInnerAudioContext = () => {
    lastAudio = makeAudio()
    if (global.__dropNextSeek) { lastAudio.dropSeek = true; global.__dropNextSeek = false }
    return lastAudio
  }
  global.wx.vibrateShort = () => {}
  global.wx.showModal = () => {}
  global.wx.navigateTo = () => {}
  let readerCfg = null
  global.Page = (opts) => { readerCfg = opts }
  require(path.join(__dirname, '..', 'miniapp', 'pages', 'reading-pkg', 'reader', 'reader.js'))
  const tick = () => new Promise((r) => setTimeout(r, 0))
  function newReader() {
    const p = Object.create(readerCfg)
    p.data = JSON.parse(JSON.stringify(readerCfg.data))
    p.setData = function (patch) { Object.assign(this.data, patch) }
    return p
  }

  // 入口只带 book_id/child_id（书架/首页）→ 时长必须从详情补全
  api.getBookDetail = async () => ({ ...detail, audio_url: '/api/miniapp/books/31/audio', audio_duration: 178 })
  const p1 = newReader()
  await p1.onLoad({ book_id: '31', child_id: '7' })
  await tick(); await tick()
  check('reader 详情补全 → 总时长 2:58 / 滑块满量程 178',
    p1.data.displayDuration === '2:58' && p1.data.sliderMax === 178 && p1.data.durationKnown === true,
    JSON.stringify({ d: p1.data.displayDuration, m: p1.data.sliderMax }))
  check('reader 音频实例带 child_id（会员门禁上下文）',
    !!lastAudio && String(lastAudio.src).indexOf('child_id=7') !== -1, lastAudio && lastAudio.src)

  // iOS：audio.duration 恒 0 → 进度/时长仍要正确、滑块不许卡最右
  lastAudio.currentTime = 35
  lastAudio.emit('timeupdate')
  check('iOS duration=0 → 已播 0:35 / 总时长 2:58 / 滑块 35',
    p1.data.displayTime === '0:35' && p1.data.displayDuration === '2:58' && p1.data.sliderValue === 35,
    JSON.stringify({ cur: p1.data.displayTime, dur: p1.data.displayDuration, v: p1.data.sliderValue }))

  // 拖动：时间回调不许把拇指顶回去；松手才 seek
  lastAudio.emit('play')
  p1.onSliderChanging({ detail: { value: 90 } })
  lastAudio.currentTime = 40
  lastAudio.emit('timeupdate')
  check('拖动中 onTimeUpdate 不回写滑块', p1.data.sliderValue === 35,
    String(p1.data.sliderValue))
  p1.onSliderChange({ detail: { value: 90 } })
  check('松手 seek 到 90 秒且 UI 同步',
    lastAudio.seekCalls.indexOf(90) !== -1 && p1.data.sliderValue === 90 && p1.data.displayTime === '1:30',
    JSON.stringify({ seeks: lastAudio.seekCalls, v: p1.data.sliderValue }))

  // 结束：回满格 + 结束态（主键变重听）
  lastAudio.duration = 178
  lastAudio.currentTime = 0
  lastAudio.emit('ended')
  check('播完 → 满格/2:58/ended 态',
    p1.data.playState === 'ended' && p1.data.sliderValue === 178 && p1.data.displayTime === '2:58',
    JSON.stringify({ s: p1.data.playState, v: p1.data.sliderValue, t: p1.data.displayTime }))

  // 详情也没给时长（老书/探测失败）→ 显示 --:-- + 滑块禁用，不许假装 0:00/最右
  api.getBookDetail = async () => ({ ...detail, audio_url: '/api/miniapp/books/31/audio', audio_duration: null })
  const p2 = newReader()
  await p2.onLoad({ book_id: '31', child_id: '7' })
  await tick(); await tick()
  check('时长未知 → 显示 --:-- 且滑块值 0',
    p2.data.displayDuration === '--:--' && p2.data.sliderValue === 0 && p2.data.durationKnown === false,
    JSON.stringify({ d: p2.data.displayDuration, v: p2.data.sliderValue }))
  lastAudio.duration = 100
  lastAudio.emit('canplay')
  check('时长未知 → 播放器自报时长回填 1:40',
    p2.data.durationKnown === true && p2.data.displayDuration === '1:40' && p2.data.sliderMax === 100,
    JSON.stringify({ d: p2.data.displayDuration, m: p2.data.sliderMax }))

  // 续播竞态（甲方 2026-10-30 报障）：进度接口先回、音频实例后建，或 iOS canplay 要等 play
  // 才触发——两种顺序都必须在"按下播放"之前落到 last_position，不能从头出声。
  let resolveDetail = null
  api.getBookDetail = () => new Promise((r) => { resolveDetail = r })
  api.getProgress = async () => ({ coverage_percent: 20, finished: false, last_position: 30 })
  const p3 = newReader()
  await p3.onLoad({ book_id: '31', child_id: '7' })
  await tick()
  check('续播竞态：进度先到、音频未建时不崩且记住进度',
    p3._audio === null && p3.data.lastPosition === 30,
    JSON.stringify({ audio: !!p3._audio, last: p3.data.lastPosition }))
  resolveDetail({ ...detail, audio_url: '/api/miniapp/books/31/audio', audio_duration: 178 })
  await tick(); await tick()
  check('音频实例建好即补续播（seek 30 先于任何 play）',
    !!p3._audio && lastAudio.seekCalls.indexOf(30) !== -1 && p3.data.displayTime === '0:30',
    JSON.stringify({ seeks: lastAudio.seekCalls, t: p3.data.displayTime }))
  // 起播闸：即使前面的续播都没赶上，按播放键前也必须 seek 到 last_position
  p3._resumeApplied = false
  p3.setData({ lastPosition: 30, finished: false })
  lastAudio.seekCalls.length = 0
  p3.onTogglePlay()
  check('按播放前兜底续播：seek(30) 与 play 同批发生',
    lastAudio.seekCalls.indexOf(30) !== -1 && lastAudio.playCalls > 0,
    JSON.stringify({ seeks: lastAudio.seekCalls, plays: lastAudio.playCalls }))

  // 音频未就绪时 seek 会被播放器丢弃（2026-10-30 模拟器实证）：不能凭调用过 seek 就认账，
  // 必须在 onSeeked 确认落点；否则 canplay/onPlay 的补 seek 全被跳过 = 永远从头听。
  api.getProgress = async () => ({ coverage_percent: 20, finished: false, last_position: 30 })
  api.getBookDetail = async () => ({ ...detail, audio_url: '/api/miniapp/books/31/audio', audio_duration: 178 })
  global.__dropNextSeek = true
  const p4 = newReader()
  await p4.onLoad({ book_id: '31', child_id: '7' })
  await tick(); await tick()
  check('seek 被丢弃时不认账（等确认）',
    !!lastAudio && p4.data.lastPosition === 30 && p4._resumeApplied === false,
    JSON.stringify({ applies: p4._resumeApplied, seeks: lastAudio && lastAudio.seekCalls }))
  lastAudio.dropSeek = false
  lastAudio.emit('play')
  check('起播后补 seek 并确认续播',
    lastAudio.seekCalls.indexOf(30) !== lastAudio.seekCalls.lastIndexOf(30) && p4._resumeApplied === true,
    JSON.stringify({ seeks: lastAudio.seekCalls, applies: p4._resumeApplied }))

  if (failures.length) {
    console.log(`\nFAIL: ${failures.length} 项行为断言未通过`)
    failures.forEach((f) => console.log(`  - ${f}`))
    process.exit(1)
  }
  console.log('\nPASS: 小程序工具层行为自检全绿（withHeaders / listMeta / onImgError / reader 播放器 / 续播竞态）✓')
})()
