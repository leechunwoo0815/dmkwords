// miniapp/utils/media.js — 媒体资源 URL 补全（后端返回相对路径，小程序 image/audio 需要绝对 URL）

function getBaseURL() {
  const app = getApp()
  return app && app.globalData.baseURL ? app.globalData.baseURL : ''
}

function appendToken(url) {
  if (!url) return url
  const token = wx.getStorageSync('token')
  if (!token) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(token)}`
}

function fullUrl(url, withToken = false) {
  if (!url) return ''
  if (/^https?:\/\//.test(url)) return withToken ? appendToken(url) : url
  const base = getBaseURL()
  if (!base) return url
  const sep = url.startsWith('/') ? '' : '/'
  const full = `${base}${sep}${url}`
  return withToken ? appendToken(full) : full
}

function formatBook(book) {
  if (!book) return book
  return {
    ...book,
    cover_url: fullUrl(book.cover_url, true),
    audio_url: fullUrl(book.audio_url),
  }
}

function formatBooks(books) {
  if (!Array.isArray(books)) return []
  return books.map(formatBook)
}

module.exports = {
  fullUrl,
  formatBook,
  formatBooks,
}
