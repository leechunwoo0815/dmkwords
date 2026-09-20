// pages/activity-pkg/activity-list/activity-list.js — 活动列表（WM9）
const api = require('../../../utils/api')
const media = require('../../../utils/media')
const session = require('../../../utils/session')

const TYPE_TEXT = {
  lecture: '宣讲会', book_club: '读书会', experience_sharing: '经验交流会',
  award_ceremony: '颁奖盛典', theme_reading: '主题阅读活动', parent_child: '亲子活动',
}

Page({
  data: {
    childName: '',
    tab: 'upcoming',
    activities: [],
    myEnrollments: [],
    // 往期活动回顾（2026-09-20 C 批）：按"开始已过 1 天"取数，覆盖过期但未结束的黑洞活动
    pastActivities: [],
    loading: true,
    loadError: false,
  },

  onShow() {
    if (!session.ensureLogin()) return
    const child = session.getCurrentChild()
    this._childId = child ? child.id : null
    this.setData({ childName: child ? child.name : '' })
    if (this._childId) this.load({ silent: true })
  },

  onTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }) },

  // silent=true：回页刷新不显示骨架屏——整页卸载重挂会丢滚动位置（同详情页 2026-09-20 报障）
  async load({ silent = false } = {}) {
    if (!silent) this.setData({ loading: true })
    this.setData({ loadError: false })
    try {
      const [acts, mine, past] = await Promise.all([
        api.listActivities(this._childId),
        api.myEnrollments(this._childId),
        api.activitiesPast(30),
      ])
      // R2（插修 16）：卡片封面拼 token（书封面正解同款）；时间去掉秒级精度
      const fmt = (t) => (t ? String(t).replace('T', ' ').slice(0, 16) : '')
      const activities = (acts || []).map((a) => ({
        ...a,
        cover_url: a.cover_url ? media.fullUrl(a.cover_url, true) : '',
        start_at: fmt(a.start_at),
      }))
      // 2026-09-20：列表不再默认露出券码；状态文案与「出示签到码」入口在这里算好（wxml 只渲染）
      const STATUS_TEXT = {
        enrolled: '已报名', checked_in: '已签到', pending_payment: '待收款确认',
        refund_pending: '退款待审', refunded: '已退款', cancelled: '已取消',
      }
      const myEnrollments = (mine || []).map((m) => ({
        ...m,
        activity_start_at: fmt(m.activity_start_at),
        statusText: STATUS_TEXT[m.status] || '状态未同步',
        canShowTicket: m.status === 'enrolled',
        checkedInAtText: m.status === 'checked_in' && m.checked_in_at
          ? `已于 ${String(m.checked_in_at).replace('T', ' ').slice(11, 16)} 签到` : '',
      }))
      const pastActivities = (past && past.items ? past.items : []).map((a) => ({
        ...a,
        cover_url: a.cover_url ? media.fullUrl(a.cover_url, true) : '',
        start_at: fmt(a.start_at),
        // 参与人数：优先"实际到过"（已签到），没有签到记录就退回"报名数"
        peopleText: a.checked_in_total > 0
          ? `${a.checked_in_total} 位小朋友参加过`
          : (a.enrolled_total > 0 ? `${a.enrolled_total} 位小朋友报名` : '暂无报名记录'),
      }))
      this.setData({ activities, myEnrollments, pastActivities })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默空列表
      this.setData({ loadError: true })
    }
    finally { this.setData({ loading: false }) }
  },

  onRetryLoad() { this.load() },

  // 出示签到码 → 独立「我的入场券」页（客户 2026-09-20：码不出现在详情页/列表里）
  goTicket(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({
      url: `/pages/activity-pkg/ticket/ticket?enrollment_id=${id}&child_id=${this._childId}`,
    })
  },

  // 「我的报名」里进活动详情：报名态下详情页只显示状态条，不再出码（双入口都留着，
  // 家长想改主意取消报名时不必切回「可报名」各自找一遍）
  goMineDetail(e) {
    this.goDetail(e)
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({
      url: `/pages/activity-pkg/activity-detail/activity-detail?id=${id}&child_id=${this._childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },
})
