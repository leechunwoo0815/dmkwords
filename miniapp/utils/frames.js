// miniapp/utils/frames.js — 等级头像框清单（**生成物**，源 = scripts/gen_fix34_frames.py）
// 叠层几何：框图 440×440、头像 256 居中 → 框显示尺寸 = 头像的 FRAME_SCALE，居中偏移 -35.94%
const FRAME_SCALE = 440 / 256

const FRAMES = [
  { id: 'star', label: '星芒框', levels: 'A-B', minLevel: 1, file: '/icons/frames/star.png' },
  { id: 'silver', label: '银环框', levels: 'C-E', minLevel: 3, file: '/icons/frames/silver.png' },
  { id: 'gold', label: '金冠框', levels: 'F-H', minLevel: 6, file: '/icons/frames/gold.png' },
  { id: 'rainbow', label: '彩虹框', levels: 'I 以上', minLevel: 9, file: '/icons/frames/rainbow.png' },
]

// 等级字母（A-Z）→ 档位；未知/空等级按最低档（A 档）
function frameForLevel(level) {
  const idx = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.indexOf(String(level || 'A').toUpperCase())
  const n = idx >= 0 ? idx + 1 : 1
  let hit = FRAMES[0]
  FRAMES.forEach((f) => { if (n >= f.minLevel) hit = f })
  return hit
}

module.exports = { FRAME_SCALE, FRAMES, frameForLevel }
