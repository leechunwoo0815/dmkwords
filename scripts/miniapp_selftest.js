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

  if (failures.length) {
    console.log(`\nFAIL: ${failures.length} 项行为断言未通过`)
    failures.forEach((f) => console.log(`  - ${f}`))
    process.exit(1)
  }
  console.log('\nPASS: 小程序工具层行为自检全绿（withHeaders / listMeta / onImgError）✓')
})()
