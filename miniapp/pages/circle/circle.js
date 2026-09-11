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
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 })
      this.getTabBar().refreshBadge && this.getTabBar().refreshBadge()
    }
    this.reload()
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
    try {
      const res = await api.circlePosts(1, PAGE_SIZE)
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
      const res = await api.circlePosts(next, PAGE_SIZE)
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
  async onToggleLike(e) {
    const id = e.currentTarget.dataset.id
    const post = this.data.posts.find((p) => p.id === id)
    if (!post) return
    const child = session.getCurrentChild()
    try {
      const res = post.liked_by_me
        ? await api.circleUnlike(id)
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
        if (!post.liked_by_me) this.reload() // 点赞后刷新头像墙
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
