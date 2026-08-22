// pages/member-pkg/checkin/checkin.js — 打卡日历（WM6）
const api = require('../../../utils/api')

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
  },

  onLoad(options) {
    this.setData({ childName: options.child_name ? decodeURIComponent(options.child_name) : '' })
    this._childId = options.child_id ? Number(options.child_id) : null
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

  async load() {
    if (!this._childId) return
    try {
      const res = await api.getCheckins(this._childId, 90)
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
    } catch (e) { /* request.js 已 toast */ }
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
