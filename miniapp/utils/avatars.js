// miniapp/utils/avatars.js — WM15 内置头像/勋章清单（**生成物，勿手改**）
// 源：backend/domain/reading_circle/art.py 的 AVATAR_IDS/BADGE_IDS
// 重新生成：python -m scripts.gen_wm15_visuals
const AVATAR_IDS = ['cat_sun', 'cat_mint', 'dog_sun', 'dog_mint', 'panda_sun', 'panda_mint', 'fox_sun', 'fox_mint', 'bunny_sun', 'bunny_mint', 'lion_sun', 'lion_mint', 'bear_sun', 'bear_mint', 'penguin_sun', 'penguin_mint', 'owl_sun', 'owl_mint', 'deer_sun', 'deer_mint', 'hedgehog_sun', 'hedgehog_mint', 'dino_sun', 'dino_mint']
const BADGE_IDS = ['milestone_m1', 'milestone_m2', 'milestone_m3', 'milestone_m4', 'milestone_m5', 'milestone_m6', 'level_template', 'streak_7', 'streak_30']
module.exports = { AVATAR_IDS, BADGE_IDS }
