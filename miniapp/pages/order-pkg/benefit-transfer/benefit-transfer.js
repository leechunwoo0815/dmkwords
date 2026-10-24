// pages/order-pkg/benefit-transfer/benefit-transfer.js — 权益转让（WM10）
const api = require('../../../utils/api')
const { loadConfigs, getConfigs } = require('../../../utils/configs')
const session = require('../../../utils/session')
const { memberStatusText } = require('../../../utils/labels')

Page({
  data: {
    cfg: getConfigs(),  // 业务口径（后端下发；见 utils/configs.js）
    children: [],
    sourceId: null,
    targetId: null,
    conditions: null,
    allOk: false,
    condError: '',   // P2-16（第九轮审查）：条件查询失败原因（原先失败与"条件不全"同渲染）
    records: [],
    listError: '',   // 第一百三十四批：转让记录加载失败（原先 catch 只把 loading 置 false，
                     // 页面等于静默丢列表——失败与"没有转让记录"同形）
    loading: true,
  },

  async onShow() {
    const { configs } = await loadConfigs()
    this.setData({ cfg: configs })
    if (!session.ensureLogin()) return
    this.load()
  },

  async load() {
    // F-L13/T34：三连 setData 合并（竞态序号守卫未做，LOW 留痕——响应乱序窗口极小）
    // member_status 是后端枚举（formal/none/…），必须过一遍中文化再进 WXML
    const children = session.getChildren().map(
      c => ({ ...c, memberLabel: memberStatusText(c.member_status) })
    )
    this.setData({ loading: true, children, listError: '' })
    try {
      const records = await api.myTransfers()
      this.setData({ records: records || [], loading: false })
    } catch (e) {
      this.setData({
        records: [],
        loading: false,
        listError: (e && e.message) || '转让记录加载失败',
      })
    }
  },

  onRetryList() { this.load() },

  onPickSource(e) {
    const id = Number(e.currentTarget.dataset.id)
    this.setData({ sourceId: id })
    this.check()
  },

  onPickTarget(e) {
    const id = Number(e.currentTarget.dataset.id)
    this.setData({ targetId: id })
    this.check()
  },

  async check() {
    const { sourceId, targetId } = this.data
    if (!sourceId || !targetId || sourceId === targetId) {
      this.setData({ conditions: null, allOk: false, condError: '' })
      return
    }
    try {
      const r = await api.transferConditions(sourceId, targetId)
      const conditions = r.conditions || []
      this.setData({
        conditions,
        allOk: conditions.length > 0 && conditions.every((c) => c.ok),
        condError: '',
      })
    } catch (e) {
      // P2-16（第九轮审查）：**接口失败 ≠ 条件不满足**。原先这里把 conditions 置空 + allOk=false，
      // 页面于是只显示"条件不全，暂不能提交"——把网络故障读成了"你自己的条件有问题"。
      // 现在单独记失败原因，面板区改为如实提示 + 重试，提交按钮的文案也不再冒充条件结论。
      this.setData({
        conditions: null,
        allOk: false,
        condError: (e && e.message) || '条件查询失败',
      })
    }
  },

  onRetryCheck() { this.check() },

  async onSubmit() {
    const { sourceId, targetId, allOk } = this.data
    if (!allOk) {
      wx.showToast({ title: '还有条件不满足，看上方列表', icon: 'none' })
      return
    }
    const res = await wx.showModal({
      title: '发起转让',
      content: `提交后两个孩子相关操作冻结，管理员 ${this.data.cfg.transfer_review_timeout_hours} 小时内审核；通过后立即生效。`,
      confirmText: '提交申请',
    })
    if (!res.confirm) return
    try {
      await api.applyTransfer(sourceId, targetId)
      wx.showToast({ title: '已提交', icon: 'success' })
      this.load()
    } catch (e) { /* toast 已弹（差什么会逐条说明） */ }
  },

  async onCancel(e) {
    const id = e.currentTarget.dataset.id
    const res = await wx.showModal({ title: '撤销转让', content: '确定撤销？双方立即解锁。' })
    if (!res.confirm) return
    try {
      await api.cancelTransfer(id)
      this.load()
    } catch (err) { /* toast 已弹 */ }
  },
})
