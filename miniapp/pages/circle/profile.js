// pages/circle/profile.js — 孩子名片页（WM15-R4 社交枢纽）
// 英文名/头像/成就数据 + 勋章墙 + TA 的帖子；「分享名片」生成海报可长按保存
const api = require('../../utils/api')
const media = require('../../utils/media')
const session = require('../../utils/session')
const img = require('../../utils/img')

const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

function _fmt(n) {
  return String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

Page({
  // P3（第一百一十六批）：封面/配图加载失败兜底——清空该字段 → 走同页既有占位分支
  // （实现与用法见 utils/img.js；`data-img-path` 标的就是这里 setData 的路径）
  onImgError(e) { img.onImgError.call(this, e) },
  data: {
    childId: null,
    profile: null,
    isBirthday: false,
    birthdayText: '',
    posts: [],
    loading: true,
    loadError: false,
    // W7-09（2026-10-28）：无 child_id 时必须有可见态（原先骨架永久转）
    noChild: false,
    posterUrl: '',
  },

  onLoad(options) {
    // W7-09：缺参时回落到当前孩子；仍没有 → 可见"请先选择孩子"，不再永久骨架
    const me = session.getCurrentChild ? session.getCurrentChild() : null
    const id = Number(options.child_id) || (me && me.id ? me.id : 0) || null
    this.setData({ childId: id, noChild: !id, loading: !!id })
  },

  onShow() {
    if (this.data.childId) this.load()
    else this.setData({ loading: false, noChild: true })
  },

  async load() {
    this.setData({ loading: true, loadError: false })
    try {
      const p = await api.circleChildProfile(this.data.childId)
      // fix34 R6 生日彩蛋：后端只回布尔（不泄露生日日期）；文案区分"看自己"与"看别人"
      const me = session.getCurrentChild()
      const isMine = !!me && Number(me.id) === Number(p.child_id)
      const birthdayText = p.is_birthday
        ? isMine
          ? '今天是我的生日！'
          : `今天是 ${p.english_name} 的生日，送上祝福吧`
        : ''
      this.setData({
        profile: {
          ...p,
          avatarUrl: p.avatar ? `/icons/avatars/${p.avatar}.png` : DEFAULT_AVATAR,
          wordsText: _fmt(p.words_total),
          badges: (p.badges || []).map((b) => ({
            ...b,
            iconUrl: `/icons/badges/${b.badge_id}.png`,
          })),
        },
        isBirthday: !!p.is_birthday,
        birthdayText,
        posts: (p.posts || []).map((x) => ({
          ...x,
          thumbUrl: x.thumb_url ? media.fullUrl(x.thumb_url, true) : '',
          imageUrlFull: x.image_url ? media.fullUrl(x.image_url, true) : '',
          avatarUrl: x.avatar ? `/icons/avatars/${x.avatar}.png` : DEFAULT_AVATAR,
        })),
      })
    } catch (e) {
      this.setData({ loadError: true })
    } finally {
      this.setData({ loading: false })
    }
  },

  onRetryLoad() { this.load() },

  // 分享名片 → 生成海报 → 全屏预览（长按保存/转发）
  async onSharePoster() {
    if (!this.data.profile) return
    wx.showLoading({ title: '生成名片中…' })
    const url = media.fullUrl(api.circleChildPosterUrl(this.data.childId), true)
    this.setData({ posterUrl: url })
    // R11-d2/M8：原先 hideLoading 在 previewImage 之前、且 previewImage 无 fail——
    // 图 404/401 时加载提示先消失，家长看不到任何反馈。
    try {
      await new Promise((resolve, reject) => {
        wx.previewImage({ urls: [url], success: resolve, fail: reject })
      })
    } catch (e) {
      wx.showToast({ title: '名片生成失败，请重试', icon: 'none' })
    } finally {
      wx.hideLoading()
    }
  },

  onPreviewPost(e) {
    const url = e.currentTarget.dataset.url
    if (url) wx.previewImage({ urls: [url] })
  },
})
