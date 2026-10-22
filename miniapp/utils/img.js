// miniapp/utils/img.js — <image> 加载失败的统一兜底（P3 第一百一十六批）
//
// 为什么需要（第八轮 P3 实测）：全仓 135 个 `<image>` 里 **`binderror` 出现 0 次**。
// 封面/头像/活动封面/帖子配图全部来自后端（URL 由 `media.fullUrl` 拼 baseURL + token），
// 一旦文件被运维删了、token 过期、或图片接口 5xx，`<image>` 会停在**空白/裂图**上——
// 家长看到的是"这本书没有封面""孩子头像没了"，而不是"加载失败"。
//
// 兜底方式为什么是"把该字段清空"而不是塞一个占位图：
// 各页早已写好 `wx:if="{{item.cover_url}}"` + `wx:else` 的**本地占位分支**
// （封面 `/icons/ui/cover.png`、头像默认猫…），清空字段就自动落到那条分支上——
// **复用既有占位样式，零 CSS 风险**（不新增组件、不改布局、不需要真机确认）。
//
// 用法（一个 data 属性 + 一个 handler）：
//   <image wx:if="{{item.cover_url}}" src="{{item.cover_url}}"
//     data-img-path="books[{{index}}].cover_url" binderror="onImgError" />
//   // 页面 js
//   const img = require('../../../utils/img')
//   onImgError(e) { img.onImgError.call(this, e) },
//
// 标注的是**要清空的 data 路径**（与 `this.setData` 的路径写法完全一致）：
// 数组元素写 `list[{{index}}].key`，对象字段写 `obj.key`，标量写 `字段名`。

/** 按 `data-img-path` 清空该字段 → 节点 `wx:if` 翻到同页既有占位分支。
 *
 * `this` 必须是页面实例（调用点一律 `img.onImgError.call(this, e)`）。
 * 没给 `data-img-path` 时**静默不动**：兜底是次级功能，不许因为兜底本身再抛一次错
 * （一次失败渲染两次报错，只会把真因埋得更深）。
 */
function onImgError(e) {
  const path = (e && e.currentTarget && e.currentTarget.dataset.imgPath) || ''
  if (!path) return
  this.setData({ [path]: '' })
}

module.exports = { onImgError }
