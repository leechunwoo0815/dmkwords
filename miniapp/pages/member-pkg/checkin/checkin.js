// pages/member-pkg/checkin/checkin.js — 打卡日历（WM6）
const api = require('../../../utils/api')
const session = require('../../../utils/session')

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

Page({
  data: {
    childName: '',
    year: 0,
    month: 0,
    monthText: '',
    weekdays: WEEKDAYS,
    days: [],
    todayChecked: false,
    currentStreak: 0,
    checkinDates: [],
    loadError: '',  // P2-45：失败态（原先失败静默 → 渲染成"连续 0 天"）
    noChild: false, // 同族补齐（第一百一十六批）：无孩子时显式空态，不渲染"本月没打卡"的假象
  },

  onLoad(options) {
    // W7-12：缺参回落到当前孩子
    this.setData({ childName: options.child_name ? decodeURIComponent(options.child_name) : ((session.getCurrentChild() || {}).name || '') })
    // 同族补齐（第一百一十六批）：统一解析（URL 合法才用，否则回落当前孩子）——
    // 原写法拿不到 child_id 就静默早退，日历照渲一屏"本月没打卡、连续 0 天"（禁假 0 同族）
    this._childId = session.resolveChildId(options)
    const now = new Date()
    this._setMonth(now.getFullYear(), now.getMonth() + 1)
    this.load()
  },

  _setMonth(y, m) {
    const first = new Date(y, m - 1, 1)
    const daysInMonth = new Date(y, m, 0).getDate()
    const lead = first.getDay()
    const days = []
    for (let i = 0; i < lead; i++) days.push({ blank: true })
    const today = new Date()
    for (let i = 1; i <= daysInMonth; i++) {
      days.push({
        day: i,
        isToday: today.getFullYear() === y && today.getMonth() + 1 === m && today.getDate() === i,
      })
    }
    this.setData({ year: y, month: m, monthText: `${y} 年 ${m} 月`, days })
  },

  /** 当前所看月份的日期区间（ISO）——**按区间取数**，翻到哪个月就拉哪个月。

   *  原先固定请求「最近 90 条」：往前翻月超过这个窗口就一片空白，家长以为孩子那几个月
   *  没坚持（P3 第一百一十六批）。区间模式由后端新增，`today_checked`/`current_streak`
   *  已与取数区间解耦（否则翻历史月份会把连续天数算成 0）。
   */
  _monthRange() {
    const { year, month } = this.data
    const pad = (n) => String(n).padStart(2, '0')
    const lastDay = new Date(year, month, 0).getDate()
    return {
      startDate: `${year}-${pad(month)}-01`,
      endDate: `${year}-${pad(month)}-${pad(lastDay)}`,
    }
  },

  onRetryLoad() { this.load() },

  async load() {
    if (!this._childId) {
      this.setData({ noChild: true, loadError: '', checkinDates: [], todayChecked: false, currentStreak: 0 })
      return
    }
    this.setData({ loadError: '' })
    try {
      const res = await api.getCheckins(this._childId, 60, this._monthRange())
      const dates = res.dates || []
      const key = `${this.data.year}-${String(this.data.month).padStart(2, '0')}`
      const days = this.data.days.map((d) =>
        d.blank ? d : { ...d, checked: dates.indexOf(`${key}-${String(d.day).padStart(2, '0')}`) !== -1 }
      )
      this.setData({
        checkinDates: dates,
        days,
        todayChecked: !!res.today_checked,
        currentStreak: res.current_streak || 0,
      })
    } catch (e) {
      // P2-45：失败**不许**渲染成"连续 0 天 / 本月没打卡"（家长以为记录丢了）
      this.setData({ loadError: (e && e.message) || '加载失败', checkinDates: [] })
    }
  },

  onPrevMonth() {
    let { year, month } = this.data
    month -= 1
    if (month === 0) { month = 12; year -= 1 }
    this._setMonth(year, month)
    this.load()
  },

  onNextMonth() {
    let { year, month } = this.data
    month += 1
    if (month === 13) { month = 1; year += 1 }
    this._setMonth(year, month)
    this.load()
  },
})
