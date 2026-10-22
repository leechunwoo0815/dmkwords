// pages/index/index.js — 首页 v5：孩子的今日任务台（续听/打卡/数据/真推荐/快捷入口）
const api = require('../../utils/api')
const session = require('../../utils/session')
const media = require('../../utils/media')
const img = require('../../utils/img')
// P3（第一百一十三批）：会员状态文案收编到 `utils/labels.js`（原先首页自建第二份副本）
const { MEMBER_STATUS_TEXT } = require('../../utils/labels')

Page({
  // P3（第一百一十六批）：封面/配图加载失败兜底——清空该字段 → 走同页既有占位分支
  // （实现与用法见 utils/img.js；`data-img-path` 标的就是这里 setData 的路径）
  onImgError(e) { img.onImgError.call(this, e) },
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
    growthBlocked: false, // 未入会等被守卫挡下（业务态）：数字不可得，页面要说清原因
    growthError: '', // P2-45：**失败态**（非 422）——与「未入会」必须分开，不能冒充业务态
    continueError: false, // P2-45：续听信息加载失败（不许看起来像「没有可续听的书」）
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
      // P3（第一百一十六批）：**不再**在此调 `refreshBadge()`。原先 tabBar 自己发一次
      // `notifications`，本页 `loadBadges` 又发一次 → 首页 onShow 9 个请求里那 1 个重复。
      // 现在红点由本页 `loadNotifications()` 统一取回后喂给 tabBar（见该方法）。
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
    // 未读消息是**家长级**（不随孩子切换清空）→ 无孩子时也要拉：提醒条与 tabBar 红点同源
    this.loadNotifications()
    if (currentChild) this.loadAll(currentChild)
  },

  loadAll(child) {
    this.loadCheckin(child.id)
    this.loadGrowth(child.id)
    this.loadContinue(child.id)
    this.loadReservationCount(child.id)
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
        growthError: '',
      })
    } catch (e) {
      // P2-45（第八轮）：**必须区分两种"拿不到"**——
      //  · 422 = 未入会（业务态）→ 显示「—」并讲清原因（"入会后可查看"）；
      //  · 其它（网络/5xx）= 失败态 → 如实说"加载失败，可重试"。
      // 原先一律按"入会后可查看"渲染：网络抖动时，已入会的家长被告知"入会后可查看"（误导）。
      // 关键：两种都要**清空**而不是静默保留，否则屏幕上会挂着上一个孩子的词数/积分。
      if (e && e.statusCode === 422) {
        this.setData({ totalWords: null, points: null, growthBlocked: true, growthError: '' })
      } else {
        this.setData({
          totalWords: null,
          points: null,
          growthBlocked: false,
          growthError: (e && e.message) || '加载失败',
        })
      }
    }
  },

  // 续听卡：最近一本"有进度未读完"（finish=0 的最近一本；读完/无进度不显示）
  async loadContinue(childId) {
    // P2-45：**失败与"没有在听的书"必须分开**。原先两个请求各自 `.catch(() => null)`：
    // 失败时卡片消失 = 看起来"没有可续听的书"（家长以为进度丢了）。
    // 现在失败进 `continueError`（如实提示"信息未加载"），只有真无进度才不显示卡片。
    // P3（第一百一十六批）：两个请求**并行**（原先串行两个 await → 白等一个 RTT）。
    const [contR, borrowR] = await Promise.allSettled([
      api.continueListening(childId),
      api.currentBorrows(childId),
    ])
    const cont = contR.status === 'fulfilled' ? contR.value : null
    const borrows = borrowR.status === 'fulfilled' ? borrowR.value : null
    const failed = contR.status === 'rejected' || borrowR.status === 'rejected'
    this.setData({
      borrowCount: borrows ? borrows.length : null,
      continueError: failed && !cont,
    })
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

  // 提醒条：预约中数量（未读消息已拆到 `loadNotifications`——它是家长级且要喂 tabBar）
  async loadReservationCount(childId) {
    try {
      const rows = await api.listReservations(childId)
      // F-M1/T26：后端枚举是 active（waiting/ready 不存在，枚举错配同族第 4 案）
      const active = (rows || []).filter((r) => r.status === 'active')
      this.setData({ reservationCount: active.length })
    } catch (e) {
      this.setData({ reservationCount: null }) // 取不到 = 未知（不许留着上一个孩子的计数）
    }
  },

  /** 消息中心**一次请求，两处消费**：页面「N 条新消息」+ 底部 tabBar 的阅读圈红点。

   *  为什么要合并（P3 第一百一十六批）：tabBar 的 `refreshBadge()` 原先自己再发一次
   *  同样的 `GET /api/miniapp/notifications?page=1&page_size=1`，首页每次 onShow 因此
   *  白多一个请求（onShow 扇出 9 个里的那 1 个重复）。红点口径不变——仍是
   *  `unread_by_scene.circle_liked`，只是数据改由本页取回后 `applyNotifications` 转发。
   *  拉不到时**保留旧值**（不清成 0 → 不制造"没有新消息"的假象）。
   */
  async loadNotifications() {
    try {
      const r = await api.notifications(1, 1)
      this.setData({ unreadCount: r.unread || 0 })
      const bar = typeof this.getTabBar === 'function' ? this.getTabBar() : null
      if (bar && bar.applyNotifications) bar.applyNotifications(r)
    } catch (e) { /* 未读数取不到：保留屏幕上的旧值（不冒充 0） */ }
  },

  // 今日推荐：从书库取 6 本真实书目（封面横滑卡）
  async loadRecommend() {
    try {
      // P2-12（2026-10-09 审查）：`listBooks` 收**对象参数**，原先按位置传（'', 1, 6）→
      // 形状不符，实际按默认 20 条拉取（首页"推荐"多拉了两倍多）。
      const res = await api.listBooks({ keyword: '', page: 1, page_size: 6 })
      this.setData({ recommend: media.formatBooks(res.items || []) })
    } catch (e) { /* 静默 */ }
  },

  goContinue() {
    const b = this.data.continueBook
    const c = this.data.currentChild
    if (!b || !c) return
    // P1-6（第八轮大审查）：同书架——`/continue-listening` 返回的 book 是 ORM 实体
    // （只有 audio_path，**没有 audio_url**），整对象进 URL 一样会让 reader 判不出"要补详情"。
    const bookId = b.book_id || b.id
    if (!bookId) return
    wx.navigateTo({
      url: `/pages/reading-pkg/reader/reader?book_id=${bookId}&child_id=${c.id}`,
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
