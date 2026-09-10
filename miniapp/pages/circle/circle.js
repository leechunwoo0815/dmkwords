// pages/circle/circle.js — 阅读圈信息流（WM14-A）
// 时间倒序真分页 + 下拉刷新 + 点卡全屏预览（长按保存）+ 点赞 + 馆长赞金色态
const api = require('../../utils/api')
const media = require('../../utils/media')
const session = require('../../utils/session')

const PAGE_SIZE = 10

// 千分位（横幅数字可读性：384000 → 384,000）
function _fmt(n) {
  return String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

Page({
  data: {
    posts: [],
    page: 1,
    total: 0,
    loading: true,
    loadError: false,
    finished: false,
    // WM14-B 社区横幅（本周全馆共读词数/人数；空则不显示）
    bannerWordsText: '',
    bannerKids: 0,
  },

  onShow() {
    if (!session.ensureLogin()) return
    if (typeof this.getTabBar === 'function' && this.getTabBar()) {
      this.getTabBar().setData({ selected: 3 })
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

  // 卡片图 URL 走 fullUrl 拼 token（媒体消费点纪律——image 组件无 Authorization）
  _decorate(items) {
    return items.map((p) => ({
      ...p,
      image_url: p.image_url ? media.fullUrl(p.image_url, true) : '',
    }))
  },

  onRetryLoad() { this.reload() },

  // 点卡片全屏预览（wx.previewImage 长按自然支持保存）
  onPreviewCard(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.previewImage({ urls: [url] })
  },

  // 点赞/取消（一心一赞）
  async onToggleLike(e) {
    const id = e.currentTarget.dataset.id
    const post = this.data.posts.find((p) => p.id === id)
    if (!post) return
    try {
      const res = post.liked_by_me
        ? await api.circleUnlike(id)
        : await api.circleLike(id)
      this.setData({
        posts: this.data.posts.map((p) =>
          p.id === id ? { ...p, liked_by_me: !post.liked_by_me, like_count: res.like_count } : p,
        ),
      })
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
