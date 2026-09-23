// pages/index/index.js — 首页 v5：孩子的今日任务台（续听/打卡/数据/真推荐/快捷入口）
const api = require('../../utils/api')
const session = require('../../utils/session')
const media = require('../../utils/media')

const MEMBER_STATUS_TEXT = {
  none: '未入会', observation: '观察期', pending_evaluation: '待评估',
  formal: '正式会员', expired: '已过期', withdrawn: '已退会',
}

Page({
  data: {
    parent: null,
    currentChild: null,
    statusText: '',
    todayChecked: false,
    // 连续打卡（null = 未知，渲染成「—」；不拿 0 冒充）
    currentStreak: null,
    // 续听卡（在借第一本 + 真实进度）
    continueBook: null,
    // 今日数据条（null = 未知/不可见 → 「—」）
    totalWords: null,
    points: null,
    growthBlocked: false, // 未入会等被守卫挡下：数字不可得，页面要说清原因
    // 提醒条（unreadCount 是家长级，不随孩子切换清空）
    unreadCount: 0,
    borrowCount: null,
    reservationCount: null,
    // 今日推荐（真实书目，横滑）
    recommend: [],
    // T45（FEAT-082）：活动轮播位（有封面未开始 ≤5）
    carousel: [],
  },

  /** 换孩子时必须清空的一整套"属于某个孩子"的状态。
   *  为什么必须有：切换后若新孩子的数据拉不到（如未入会被 422 挡下），
   *  旧的静默 catch 会把**上一个孩子的数字继续挂在屏幕上**——
   *  用户实测「切换了孩子，打卡天数同步了，积分和词数还是显示上一个孩子的」（2026-09-21）。
   *  这里先清场再加载：任何失败都只会显示「—」，绝不显示别人的数据（禁假 0 同族）。 */
  _childScopedReset() {
    this.setData({
      todayChecked: false,
      currentStreak: null,
      totalWords: null,
      points: null,
      growthBlocked: false,
      continueBook: null,
      borrowCount: null,
      reservationCount: null,
    })
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.refresh()
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 0 })
      this.getTabBar().refreshBadge && this.getTabBar().refreshBadge() // fix34b：每个 tab 页各自刷新（红点只活在首页实例上=原 bug）
    }
  },

  refresh() {
    const parent = session.getParent()
    const currentChild = session.getCurrentChild()
    // 孩子换了（「我的」页切的孩子）→ 先清场再加载，绝不拿上一个孩子的数字顶着
    if (this._loadedChildId !== (currentChild ? currentChild.id : null)) {
      this._loadedChildId = currentChild ? currentChild.id : null
      this._childScopedReset()
    }
    this.setData({
      parent,
      currentChild,
      statusText: currentChild ? (MEMBER_STATUS_TEXT[currentChild.member_status] || currentChild.member_status) : '',
    })
    if (currentChild) this.loadAll(currentChild)
  },

  loadAll(child) {
    this.loadCheckin(child.id)
    this.loadGrowth(child.id)
    this.loadContinue(child.id)
    this.loadBadges(child.id)
    this.loadRecommend()
    this.loadCarousel()
  },

  goActivityDetail(e) {
    const id = e.currentTarget.dataset.id
    const c = session.getCurrentChild()
    if (id) {
      const childParam = c ? `&child_id=${c.id}` : ''
      wx.navigateTo({ url: `/pages/activity-pkg/activity-detail/activity-detail?id=${id}${childParam}` })
    }
  },

  async loadCarousel() {
    try {
      const res = await api.activityCarousel()
      // R2（插修 16）：封面 URL 走 fullUrl 拼 token（书封面正解同款——
      // <image> 相对路径无 token=401 裂图）；start_at ISO 串顺带格式化
      const carousel = (res.items || []).map((a) => ({
        ...a,
        cover_url: a.cover_url ? media.fullUrl(a.cover_url, true) : '',
        start_at: (a.start_at || '').slice(0, 16).replace('T', ' '),
      }))
      this.setData({ carousel })
    } catch (e) { /* 轮播失败静默（非关键路径） */ }
  },

  async loadCheckin(childId) {
    try {
      const res = await api.getCheckins(childId, 60)
      this.setData({ todayChecked: !!res.today_checked, currentStreak: res.current_streak || 0 })
    } catch (e) {
      // 拉不到 → 未知（null 渲染「—」）。**不许保留上一个孩子的天数**：静默 catch 会把
      // 别人的数据留在屏幕上（2026-09-21 用户实测的那半个 bug）
      this.setData({ todayChecked: false, currentStreak: null })
    }
  },

  async loadGrowth(childId) {
    try {
      const g = await api.growthSummary(childId)
      this.setData({
        totalWords: g.words_total || 0,
        points: g.points_total || 0,
        growthBlocked: false,
      })
    } catch (e) {
      // 未入会的孩子取不到成长数据（后端 422「入会后可查看」）→ 显示「—」并把原因讲清楚；
      // 关键：这里必须**清空**而不是静默保留，否则屏幕上会挂着上一个孩子的词数/积分
      this.setData({ totalWords: null, points: null, growthBlocked: true })
    }
  },

  // 续听卡：最近一本"有进度未读完"（finish=0 的最近一本；读完/无进度不显示）
  async loadContinue(childId) {
    // 两个请求各自兜底：都失败也只是「未知」（null），不会把上一个孩子的数字留在屏幕上
    const [cont, borrows] = await Promise.all([
      api.continueListening(childId).catch(() => null),
      api.currentBorrows(childId).catch(() => null),
    ])
    this.setData({ borrowCount: borrows ? borrows.length : null })
    if (!cont || !cont.book) {
      this.setData({ continueBook: null })
      return
    }
    this.setData({
      continueBook: {
        ...media.formatBook(cont.book),
        id: cont.book.id,
        percent: cont.percent || 0,
        lastPosition: cont.last_position || 0,
        dueText: cont.due_at ? this.dueText(cont) : '可续听',
      },
    })
  },

  dueText(borrow) {
    const due = borrow.due_date || borrow.due_at
    if (!due) return ''
    const days = Math.ceil((new Date(String(due).replace(/-/g, '/')) - new Date()) / 86400000)
    if (days < 0) return `已逾期 ${-days} 天`
    if (days === 0) return '今天到期'
    return `${days} 天后到期`
  },

  // 提醒条：未读消息 + 预约中数量
  async loadBadges(childId) {
    // F-L14/T34：两独立请求并行（原串行 await 拖慢首页徽标）
    const [nRes, rsRes] = await Promise.allSettled([
      api.notifications(1, 1),
      api.listReservations(childId),
    ])
    if (nRes.status === 'fulfilled') {
      this.setData({ unreadCount: nRes.value.unread || 0 })
    }
    if (rsRes.status === 'fulfilled') {
      // F-M1/T26：后端枚举是 active（waiting/ready 不存在，枚举错配同族第 4 案）
      const active = (rsRes.value || []).filter((r) => r.status === 'active')
      this.setData({ reservationCount: active.length })
    } else {
      this.setData({ reservationCount: null }) // 取不到 = 未知（不许留着上一个孩子的计数）
    }
  },

  // 今日推荐：从书库取 6 本真实书目（封面横滑卡）
  async loadRecommend() {
    try {
      const res = await api.listBooks('', 1, 6)
      this.setData({ recommend: media.formatBooks(res.items || []) })
    } catch (e) { /* 静默 */ }
  },

  goContinue() {
    const b = this.data.continueBook
    const c = this.data.currentChild
    if (!b || !c) return
    wx.navigateTo({
      url: `/pages/reading-pkg/reader/reader?book=${encodeURIComponent(JSON.stringify(b))}&child_id=${c.id}`,
    })
  },
  goRecommend(e) {
    const book = e.currentTarget.dataset.book
    const c = this.data.currentChild
    if (!book) return
    // F-L19：整对象进 URL 改传 id
    wx.navigateTo({
      url: `/pages/reading-pkg/book-detail/book-detail?book_id=${book.book_id ?? book.id}${c ? `&child_id=${c.id}` : ''}`,
    })
  },
  goMoreBooks() { wx.switchTab({ url: '/pages/books/books' }) },
  goMessage() { wx.navigateTo({ url: '/pages/order-pkg/messages/messages' }) },
  goShelf() { wx.switchTab({ url: '/pages/shelf/shelf' }) },
  goCheckin() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/checkin/checkin?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  // 会员码（2026-09-21 B 批）：到店出示给馆员扫码识别身份。码由后端下发（不可枚举，含校验位），
  // 这里只带当前孩子跳转，码在目标页从 session 缓存读（不在这里算、也不拼 id）。
  goMemberCode() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/member-code/member-code?child_id=${c.id}` })
  },
  goAchievement() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/achievement/achievement?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goReservation() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/order-pkg/reservation/reservation?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goLeaderboard() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/leaderboard/leaderboard?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goPassport() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/member-pkg/profile-card/profile-card?child_id=${c.id}&child_name=${encodeURIComponent(c.name)}` })
  },
  goActivities() {
    wx.navigateTo({ url: '/pages/activity-pkg/activity-list/activity-list' })
  },
  goVocabulary() {
    const c = this.data.currentChild
    if (!c) return
    wx.navigateTo({ url: `/pages/reading-pkg/vocabulary/vocabulary?child_id=${c.id}` })
  },
})
