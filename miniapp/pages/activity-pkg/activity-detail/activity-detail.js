// pages/activity-pkg/activity-detail/activity-detail.js — 活动详情与报名（WM9）
const api = require('../../../utils/api')

const STATUS_TEXT = {
  enrolled: '已报名', checked_in: '已签到', pending_payment: '待收款确认',
  refund_pending: '退款待审', refunded: '已退款', cancelled: '已取消',
}

Page({
  data: {
    activity: null,
    childName: '',
    loading: true,
    enrolling: false,
  },

  onLoad(options) {
    this._activityId = Number(options.id)
    this._childId = Number(options.child_id)
    this.setData({ childName: decodeURIComponent(options.child_name || '') })
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const a = await api.activityDetail(this._activityId, this._childId)
      this.setData({ activity: a })
    } catch (e) { /* toast 已弹 */ }
    finally { this.setData({ loading: false }) }
  },

  async onEnroll() {
    const { activity, enrolling } = this.data
    if (enrolling) return
    const mine = activity.my_enrollment
    if (mine && ['enrolled', 'pending_payment', 'checked_in', 'refund_pending'].indexOf(mine.status) !== -1) {
      wx.showToast({ title: '已报名过此活动', icon: 'none' })
      return
    }
    this.setData({ enrolling: true })
    try {
      const r = await api.enrollActivity(this._activityId, this._childId)
      if (r.order_id) {
        wx.showModal({
          title: '报名成功（待收款）',
          content: `名额已保留，请到店支付 ${this.data.activity.fee_display} 完成报名（馆员确认收款后入场券生效）。`,
          showCancel: false,
        })
      } else {
        wx.showModal({
          title: '报名成功',
          content: `入场券码 ${r.enrollment.ticket_code}，活动当天出示给馆员扫码签到。`,
          showCancel: false,
        })
      }
      this.load()
    } catch (e) { /* toast 已弹（已满/仅会员/重复报名等） */ }
    finally { this.setData({ enrolling: false }) }
  },

  async onCancel() {
    const mine = this.data.activity.my_enrollment
    const res = await wx.showModal({
      title: '取消报名', content: '确定取消本次报名？（名额立即释放）',
    })
    if (!res.confirm) return
    try {
      await api.cancelEnrollment(mine.id, this._childId)
      wx.showToast({ title: '已取消', icon: 'success' })
      this.load()
    } catch (e) { /* toast 已弹 */ }
  },

  async onRefund() {
    const mine = this.data.activity.my_enrollment
    const res = await wx.showModal({
      title: '申请退款',
      content: '未签到且距开始超过 2 小时可申请全额退款（管理员审核后到账）。',
      confirmText: '申请退款',
    })
    if (!res.confirm) return
    try {
      await api.refundApplyEnrollment(mine.id, this._childId)
      wx.showToast({ title: '已提交，等待审核', icon: 'none' })
      this.load()
    } catch (e) { /* toast 已弹（已签到/临期/已开始等） */ }
  },

  copyTicket() {
    const mine = this.data.activity.my_enrollment
    if (!mine) return
    wx.setClipboardData({ data: mine.ticket_code })
  },
})
