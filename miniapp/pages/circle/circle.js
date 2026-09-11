// pages/circle/circle.js — 阅读圈信息流（WM15-R1：朋友圈式布局重构）
// 头像+名字+相对时间 → 成就文字（原生）→ 卡片缩略图 → 点赞（含头像墙 + 弹跳动效）
const api = require('../../utils/api')
const media = require('../../utils/media')
const session = require('../../utils/session')

const PAGE_SIZE = 10
const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

// 千分位（横幅/词数可读性）
function _fmt(n) {
  return String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

// C6 相对时间口径（写死）：<1min 刚刚 / <1h X 分钟前 / <24h X 小时前 /
// 自然日昨天「昨天 HH:mm」/ 今年「MM-DD」/ 跨年「YYYY-MM-DD」。
// 时区：created_at 为服务端 naive 本地串，JS Date 按本地解析 → 与 Asia/Shanghai 锚定一致。
function _relTime(s) {
  if (!s) return ''
  const t = new Date(String(s).replace(/-/g, '/')).getTime()
  if (Number.isNaN(t)) return ''
  const now = Date.now()
  const diff = now - t
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  const d = new Date(t)
  const today = new Date()
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  if (t >= startOfToday) return `${hours} 小时前`
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (t >= startOfToday - 86400000) return `昨天 ${hm}`
  const mmdd = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === today.getFullYear() ? mmdd : `${d.getFullYear()}-${mmdd}`
}

// 头像 id → 本地包路径（零加载零 token；无头像用默认）
function _avatarUrl(id) {
  return id ? `/icons/avatars/${id}.png` : DEFAULT_AVATAR
}

Page({
  data: {
    posts: [],
    page: 1,
    total: 0,
    loading: true,
    loadError: false,
    finished: false,
    // WM14-B 社区横幅
    bannerWordsText: '',
    bannerKids: 0,
    // fix34 R0：朋友圈式「谁赞了你」（顶部通知条 + 展开列表；看过即消）
    likes: [],
    likesUnread: [],
    unreadLikes: 0,
    likesOpen: false,
    likesText: '',
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 })
      this.getTabBar().refreshBadge && this.getTabBar().refreshBadge() // fix34b：每个 tab 页各自刷新（红点只活在首页实例上=原 bug）
      // 红点由组件 pageLifetimes.show 自刷新（fix34 R0：组件自治，本页不再手动调）
    }
    // fix33 R2 状态隔离：每次进页都带「当前孩子」重载——在会员页切了孩子再回来，
    // liked_by_me / 点赞主体随之切换（不缓存上一个孩子的点赞态）
    this.reload()
    this.loadLikes() // fix34 R0：顶部「谁赞了你」（未读才显示）
  },

  onPullDownRefresh() {
    this.reload().finally(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading) return
    this.loadMore()
  },

  async reload() {
    this.setData({ loading: true, loadError: false })
    // fix33 R2：请求带当前孩子 → liked_by_me 按孩子算（无孩子则为浏览态，全 false）
    this._childId = (session.getCurrentChild() || {}).id || null
    try {
      const res = await api.circlePosts(1, PAGE_SIZE, this._childId)
      const banner = res.banner || null
      this.setData({
        posts: this._decorate(res.items || []),
        page: 1,
        total: res.total || 0,
        finished: (res.items || []).length >= (res.total || 0),
        bannerWordsText: banner && banner.words > 0 ? _fmt(banner.words) : '',
        bannerKids: banner ? banner.kids || 0 : 0,
      })
    } catch (e) {
      this.setData({ loadError: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  async loadMore() {
    const next = this.data.page + 1
    this.setData({ loading: true })
    try {
      const res = await api.circlePosts(next, PAGE_SIZE, this._childId)
      const items = this._decorate(res.items || [])
      this.setData({
        posts: this.data.posts.concat(items),
        page: next,
        total: res.total || 0,
        finished: this.data.posts.length + items.length >= (res.total || 0),
      })
    } catch (e) { /* request.js 已 toast */ }
    finally { this.setData({ loading: false }) }
  },

  // 媒体消费点：缩略图（信息流小图）+ 大图（预览）都走 fullUrl 拼 token
  // 头像走**本地包**（不进 token 清单——A2 裁决）
  _decorate(items) {
    return items.map((p) => ({
      ...p,
      thumbUrl: p.thumb_url ? media.fullUrl(p.thumb_url, true) : '',
      imageUrlFull: p.image_url ? media.fullUrl(p.image_url, true) : '',
      avatarUrl: _avatarUrl(p.avatar),
      relTime: _relTime(p.created_at),
      likers: (p.likers || []).map((l) => ({ ...l, avatarUrl: _avatarUrl(l.avatar) })),
      pop: false,
    }))
  },

  onRetryLoad() { this.reload() },

  // fix34 R0/R0b：拉「收到的赞」——数据源=消息中心 circle.liked（**同一张表同一口径**，
  // 与 tab 红点同源，禁自建第二套计数）。
  // R0b 修正（用户实测）：**列表拿全量**（含已读），只有"未读数"决定高亮——
  // 原实现只留未读，导致"看过的赞"再也回看不了、且留下空面板死状态。
  async loadLikes() {
    try {
      const r = await api.notifications(1, 20, '', 'circle.liked')
      const unread = (r.unread_by_scene && r.unread_by_scene.circle_liked) || 0
      const items = (r.items || []).map((i) => ({
        id: i.id,
        // 行为主体：ref_type=child 时 ref_id=点赞孩子 id（点进 TA 的名片）
        childId: i.ref_type === 'child' ? Number(i.ref_id) : 0,
        name: (i.content || '').split(' 赞了')[0] || '小伙伴',
        avatarUrl: _avatarUrl(i.actor_avatar),
        level: i.actor_level || 'A',
        relTime: _relTime(i.created_at),
        read: !!i.read,
        refType: i.ref_type || '',
      }))
      const total = r.total || items.length
      const unreadItems = items.filter((i) => !i.read)
      // fix34e：未读的馆长赞 → 金光播报（同一 id 只弹一次，关掉时标已读）
      const adminHit = unreadItems.find((i) => i.refType === 'circle_admin')
      const celebrate =
        adminHit && adminHit.id !== this._celebratedId ? adminHit.content : ''
      const first = unreadItems[0] || items[0]
      this.setData({
        likes: items,
        // 高亮条只堆"没看过的那几个"头像（已读的不参与堆叠，避免空槽位）
        likesUnread: unreadItems,
        unreadLikes: items.length ? unread : 0,
        likesText:
          items.length && first
            ? unread > 1
              ? `${first.name} 等 ${unread} 位小伙伴赞了你的成就`
              : `${first.name} 赞了你的成就`
            : '',
        likesQuietText: total ? `收到的赞 · 共 ${total} 条` : '',
        // 没有可展示的内容时收起面板（避免"空面板还开着"这类死状态）
        likesOpen: items.length ? this.data.likesOpen : false,
      })
    } catch (e) {
      // 通知条失败不影响信息流（降级：不显示条）
      this.setData({
        likes: [],
        likesUnread: [],
        unreadLikes: 0,
        likesText: '',
        likesQuietText: '',
        likesOpen: false,
      })
    }
  },

  // 展开 = 看过了 → 把**未读**标记已读（红点与高亮同时消失），列表保留可继续看/再点收起
  onOpenLikes() {
    const opening = !this.data.likesOpen
    this.setData({ likesOpen: opening })
    if (!opening) return
    const ids = this.data.likes.filter((i) => !i.read).map((i) => i.id)
    if (!ids.length) return
    api.markNotificationsRead(ids, false).then(() => {
      // 本地同步为已读（条从"高亮"降级为"低调"，列表元素不消失）
      this.setData({
        unreadLikes: 0,
        likes: this.data.likes.map((i) => ({ ...i, read: true })),
      })
      const tb = typeof this.getTabBar === 'function' && this.getTabBar()
      if (tb && tb.refreshBadge) tb.refreshBadge()
    }).catch(() => { /* request.js 已 toast */ })
  },

  // fix34e：关掉金光播报 → 这条馆长赞标记已读（不再重复弹）+ tab 红点跟随
  onCloseCelebrate() {
    const hit = this.data.likes.find(
      (i) => !i.read && i.refType === 'circle_admin' && i.content === this.data.celebrate
    )
    this.setData({ celebrate: '' })
    if (!hit) return
    this._celebratedId = hit.id
    api.markNotificationsRead([hit.id], false).then(() => {
      this.setData({
        unreadLikes: Math.max(0, this.data.unreadLikes - 1),
        likes: this.data.likes.map((i) => (i.id === hit.id ? { ...i, read: true } : i)),
        likesUnread: this.data.likesUnread.filter((i) => i.id !== hit.id),
      })
      const tb = typeof this.getTabBar === 'function' && this.getTabBar()
      if (tb && tb.refreshBadge) tb.refreshBadge()
    }).catch(() => { /* request.js 已 toast */ })
  },

  // 列表项 → 点赞者名片页（深链同消息中心）
  onLikeItem(e) {
    const childId = Number(e.currentTarget.dataset.child)
    if (!childId) return
    wx.navigateTo({ url: `/pages/circle/profile?child_id=${childId}` })
  },

  // 点卡片 → 全屏预览大图（长按保存）
  onPreviewCard(e) {
    const url = e.currentTarget.dataset.url
    if (url) wx.previewImage({ urls: [url] })
  },

  // 点头像/名字 → 孩子名片页（R4 社交枢纽入口）
  onChildProfile(e) {
    const { child, name } = e.currentTarget.dataset
    if (!child) return
    wx.navigateTo({
      url: `/pages/circle/profile?child_id=${child}&child_name=${encodeURIComponent(name || '')}`,
    })
  },

  // 点赞/取消 + 弹跳动效（wxss transform，低成本高感知）
  // fix33 R2：点赞主体=当前孩子（未选孩子由后端 422「请先选择孩子」提示）
  async onToggleLike(e) {
    const id = e.currentTarget.dataset.id
    const post = this.data.posts.find((p) => p.id === id)
    if (!post) return
    const child = session.getCurrentChild()
    try {
      const res = post.liked_by_me
        ? await api.circleUnlike(id, child ? child.id : null)
        : await api.circleLike(id, child ? child.id : null)
      this.setData({
        posts: this.data.posts.map((p) =>
          p.id === id
            ? { ...p, liked_by_me: !post.liked_by_me, like_count: res.like_count, pop: true }
            : p,
        ),
      })
      setTimeout(() => {
        this.setData({
          posts: this.data.posts.map((p) => (p.id === id ? { ...p, pop: false } : p)),
        })
        // fix33 R3：**两个分支都刷新**——取消点赞后头像墙必须同步消失
        // （原实现在取消分支不刷新：人头像仍挂在墙上）；同时以服务端 liked_by_me 收口
        this.reload()
      }, 320)
    } catch (err) { /* request.js 已 toast */ }
  },

  // 家长删自己的帖（删除权仅家长与超管；Q9：删除后恢复晒权，可重晒）
  onDeletePost(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除帖子',
      content: '确定删除这条成就分享吗？删除后可以重新晒这张成就卡。',
      confirmText: '删除',
      confirmColor: '#EF4444',
      success: (r) => {
        if (!r.confirm) return
        api.circleDeletePost(id).then(() => {
          wx.showToast({ title: '已删除', icon: 'success' })
          this.reload()
        }).catch(() => {})
      },
    })
  },

  // 顶部「晒成就」入口（常驻入口）
  goShare() {
    const child = session.getCurrentChild()
    if (!child) {
      wx.showToast({ title: '请先在首页选择孩子', icon: 'none' })
      return
    }
    wx.navigateTo({
      url: `/pages/circle/share?child_id=${child.id}&child_name=${encodeURIComponent(child.name)}`,
    })
  },
})
