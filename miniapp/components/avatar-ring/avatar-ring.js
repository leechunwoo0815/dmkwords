// components/avatar-ring/avatar-ring.js — 头像 + 等级头像框叠层（fix34 R4 / fix34b 加固）
//
// 为什么要组件：头像框有 **5 个消费端**（信息流头像 / 名片页大头像 / 点赞头像墙 /
// 榜单页 / 我的页选择器）。逐端手写叠层几何必漂移（媒体消费点清单化纪律）。
//
// 几何（与 scripts/gen_fix34_frames.py 同源，改一处必改两处）：
//   框资产 440×440、头像 256 居中 → 框显示尺寸 = 头像 × FRAME_SCALE(1.71875)，
//   居中偏移 -(框-头像)/2；档位由 level 字母经 utils/frames.frameForLevel 映射。
//
// fix34b 加固（用户反馈"看不到框"）：① 计算同时挂在 `attached` 与 `observers`
// （只挂 observers 时属性初值无变化就不触发 → 框永远算不出来）；
// ② size 走 Number()（wxml 写 `size="84"` 是字符串，不强转就是 NaN → 框宽 NaN 不渲染）。
const { frameForLevel, FRAME_SCALE } = require('../../utils/frames')

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
  },

  lifetimes: {
    attached() {
      this._apply()
    },
  },

  observers: {
    'level, size, frame, gm': function () {
      this._apply()
    },
  },

  methods: {
    _apply() {
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
