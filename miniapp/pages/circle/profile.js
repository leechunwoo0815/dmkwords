// pages/circle/profile.js — 孩子名片页（WM15-R4 社交枢纽）
// 英文名/头像/成就数据 + 勋章墙 + TA 的帖子；「分享名片」生成海报可长按保存
const api = require('../../utils/api')
const media = require('../../utils/media')
const session = require('../../utils/session')

const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

function _fmt(n) {
  return String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

Page({
  data: {
    childId: null,
    profile: null,
    isBirthday: false,
    birthdayText: '',
    posts: [],
    loading: true,
    loadError: false,
    posterUrl: '',
  },

  onLoad(options) {
    this.setData({ childId: Number(options.child_id) || null })
  },

  onShow() {
    if (this.data.childId) this.load()
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
          ? '今天是我的生日！🎂'
          : `今天是 ${p.english_name} 的生日，送上祝福吧 🎂`
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
    wx.hideLoading()
    wx.previewImage({ urls: [url] })
  },

  onPreviewPost(e) {
    const url = e.currentTarget.dataset.url
    if (url) wx.previewImage({ urls: [url] })
  },
})
