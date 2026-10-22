// components/avatar-ring/avatar-ring.js — 头像 + 等级头像框叠层（fix34 R4 / fix34b 加固）
//
// 为什么要组件：头像框有 **6 个消费端**（信息流头像 / 名片页大头像 / 点赞头像墙 /
// 榜单页 / 我的页选择器 / 阅读护照大头像[2026-09-17 接入]）。
// 逐端手写叠层几何必漂移（媒体消费点清单化纪律）。
//
// 几何（与 scripts/gen_fix34_frames.py 同源，改一处必改两处）：
//   框资产 440×440、头像 256 居中 → 框显示尺寸 = 头像 × FRAME_SCALE(1.71875)，
//   居中偏移 -(框-头像)/2；档位由 level 字母经 utils/frames.frameForLevel 映射。
//
// fix34b 加固（用户反馈"看不到框"）：① 计算同时挂在 `attached` 与 `observers`
// （只挂 observers 时属性初值无变化就不触发 → 框永远算不出来）；
// ② size 走 Number()（wxml 写 `size="84"` 是字符串，不强转就是 NaN → 框宽 NaN 不渲染）。
//
// P3（第一百一十六批）兜底：`src` 为空或加载失败一律回落本地默认猫——属性注释早就写着
// "空则用默认猫"，但原实现把空串直接喂给 `<image>`（渲染成空白圆）。头像 id 来自后端
// `child.avatar`，若它指向一个包内不存在的资产（清单漂移），只有这条兜底能挡住白头像。
const { frameForLevel, FRAME_SCALE } = require('../../utils/frames')

/** 默认头像（本地包内资产，同 `utils/avatars.js` 清单里的第一个：cat_sun） */
const DEFAULT_AVATAR = '/icons/avatars/cat_sun.png'

Component({
  properties: {
    /** 头像图（本地包路径；空则用默认猫） */
    src: { type: String, value: '' },
    /** 孩子等级字母 A-Z（决定框档；空按最低档） */
    level: { type: String, value: 'A' },
    /** 头像直径（rpx） */
    size: { type: Number, value: 72 },
    /** false = 只要头像不要框（如头像选择器宫格） */
    frame: { type: Boolean, value: true },
    /** true = 馆长（GM）：固定用最高档「鎏金冠冕框」，不走等级映射（fix34d） */
    gm: { type: Boolean, value: false },
  },

  data: {
    frameUrl: '',
    frameSize: 0,
    offset: 0,
    /** 真正渲染的头像地址（= src，空/失败时回落默认猫） */
    shownSrc: DEFAULT_AVATAR,
  },

  lifetimes: {
    attached() {
      this._apply()
    },
  },

  observers: {
    'level, size, frame, gm': function () {
      if (this.data.level == null) this.setData({ level: 'A' });
      this._apply()
    },
    // src 变了要**重置**兜底态：换了孩子/换了头像就重新试一次，不能一直卡在默认猫上
    src: function () {
      this._applySrc()
    },
  },

  methods: {
    /** 头像地址：空/非法 → 默认猫（binderror 那条路也会回到这里） */
    _applySrc() {
      const s = this.data.src
      this.setData({ shownSrc: s && typeof s === 'string' ? s : DEFAULT_AVATAR })
    },

    /** 头像加载失败 → 兜底默认猫（该资产在包内，不会再失败） */
    onAvatarError() {
      if (this.data.shownSrc !== DEFAULT_AVATAR) this.setData({ shownSrc: DEFAULT_AVATAR })
    },

    /** 头像框加载失败 → 只留头像（框是装饰，宁可少画不画裂图） */
    onFrameError() {
      this.setData({ frameUrl: '' })
    },

    _apply() {
      this._applySrc()
      if (!this.data.frame) {
        this.setData({ frameUrl: '', frameSize: 0, offset: 0 })
        return
      }
      const size = Number(this.data.size) || 72
      // 馆长：固定最高档「鎏金冠冕框」（资产仅小程序端；几何与四档框一致）
      const f = this.data.gm ? { file: '/icons/special/gm_frame.png' } : frameForLevel(this.data.level)
      const frameSize = Math.round(size * FRAME_SCALE)
      this.setData({
        frameUrl: f && f.file ? f.file : '',
        frameSize,
        offset: -Math.round((frameSize - size) / 2),
      })
    },
  },
})
