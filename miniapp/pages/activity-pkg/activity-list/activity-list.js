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
    loading: true,
    loadError: false,
  },

  onShow() {
    if (!session.ensureLogin()) return
    const child = session.getCurrentChild()
    this._childId = child ? child.id : null
    this.setData({ childName: child ? child.name : '' })
    if (this._childId) this.load()
  },

  onTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }) },

  async load() {
    this.setData({ loading: true, loadError: false })
    try {
      const [acts, mine] = await Promise.all([
        api.listActivities(this._childId),
        api.myEnrollments(this._childId),
      ])
      // R2（插修 16）：卡片封面拼 token（书封面正解同款）
      const activities = (acts || []).map((a) => ({
        ...a,
        cover_url: a.cover_url ? media.fullUrl(a.cover_url, true) : '',
      }))
      this.setData({ activities, myEnrollments: mine || [] })
    } catch (e) {
      // F-M12/T26：fetch 失败进错误态（点击重试），不再静默空列表
      this.setData({ loadError: true })
    }
    finally { this.setData({ loading: false }) }
  },

  onRetryLoad() { this.load() },

  goDetail(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({
      url: `/pages/activity-pkg/activity-detail/activity-detail?id=${id}&child_id=${this._childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },
})
