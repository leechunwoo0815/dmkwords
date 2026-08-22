// frontend/utils/storage.js
// 本地缓存封装 — 统一 wx.setStorageSync/getStorageSync，带过期时间支持

const PREFIX = 'mw_';

module.exports = {
  set(key, value, ttlMinutes) {
    const data = {
      value,
      ts: Date.now(),
      ttl: ttlMinutes ? ttlMinutes * 60 * 1000 : null,
    };
    try {
      wx.setStorageSync(PREFIX + key, JSON.stringify(data));
    } catch (e) {
      console.error('storage.set failed:', key, e);
    }
  },

  get(key) {
    try {
      const raw = wx.getStorageSync(PREFIX + key);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (data.ttl && Date.now() - data.ts > data.ttl) {
        wx.removeStorageSync(PREFIX + key);
        return null;
      }
      return data.value;
    } catch (e) {
      return null;
    }
  },

  remove(key) {
    try { wx.removeStorageSync(PREFIX + key); } catch (e) { /* silent */ }
  },

  // 阅读进度专用
  saveReadProgress(bookId, childId, progress) {
    this.set(`read_progress_${bookId}_${childId}`, progress);
  },
  getReadProgress(bookId, childId) {
    return this.get(`read_progress_${bookId}_${childId}`);
  },

  // 测验进度专用
  saveQuizProgress(quizId, data) {
    this.set(`quiz_progress_${quizId}`, data, 1440); // 24h TTL
  },
  getQuizProgress(quizId) {
    return this.get(`quiz_progress_${quizId}`);
  },
  clearQuizProgress(quizId) {
    this.remove(`quiz_progress_${quizId}`);
  },

  // 表单草稿专用
  saveDraft(page, data) {
    this.set(`form_draft_${page}`, data, 1440); // 24h TTL
  },
  getDraft(page) {
    return this.get(`form_draft_${page}`);
  },
  clearDraft(page) {
    this.remove(`form_draft_${page}`);
  },
};
