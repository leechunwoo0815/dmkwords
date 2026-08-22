// pages/activity-pkg/activity-list/activity-list.js — 活动列表（WM9）
const api = require('../../../utils/api')
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
    this.setData({ loading: true })
    try {
      const [acts, mine] = await Promise.all([
        api.listActivities(this._childId),
        api.myEnrollments(this._childId),
      ])
      this.setData({ activities: acts || [], myEnrollments: mine || [] })
    } catch (e) { /* toast 已弹 */ }
    finally { this.setData({ loading: false }) }
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({
      url: `/pages/activity-pkg/activity-detail/activity-detail?id=${id}&child_id=${this._childId}&child_name=${encodeURIComponent(this.data.childName)}`,
    })
  },
})
