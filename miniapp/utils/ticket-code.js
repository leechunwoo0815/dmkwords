// 入场券码绘制（PRD §9.2.1 门店扫码签到）
//
// 二维码（主）+ Code 128 条形码（备）都画到 canvas 2D。纯本地计算，不发网络请求、
// 不依赖云服务出图（门店可能没外网，且券码不该出系统边界）。
//
// 颜色为什么写在 JS 里而不是 WXSS 令牌：canvas 的 fillStyle 不解析 CSS 变量，
// 无法 `var(--fg)`；且**扫码对比度优先于配色美学**，因此固定"白底 + 深墨"，
// 不走纸张米白（--bg），避免浅底降低解码率。文案侧的视觉令牌不受影响。
//
// 导出 buildQrMatrix / encodeBars 两个纯函数，便于用 node 自检（见 libs/README.md）。

const qrcode = require('../libs/qrcode.js');
const code128 = require('../libs/code128.js');

const ECC_LEVEL = 'M'; // 纠错等级：M(15%) 兼顾容错与码密度，手机屏幕显示够用
const QR_QUIET_MODULES = 4; // 二维码静默区（标准 4 模块，缺了扫码枪会对不上焦）
const BAR_QUIET_MODULES = 10; // Code128 静默区（标准 10 模块）

const INK = '#3B2F2F'; // = 令牌 --fg（深棕墨）
const PAPER = '#FFFFFF'; // 码底纯白：对比度优先，不用 --bg 米白

/**
 * 二维码模块矩阵（自动选版本）。
 * @param {string} text 券码
 * @returns {{count:number,isDark:function(number,number):boolean}}
 */
function buildQrMatrix(text) {
  const qr = qrcode(0, ECC_LEVEL); // 0 = 自动版本
  qr.addData(String(text || ''));
  qr.make();
  const count = qr.getModuleCount();
  return {
    count,
    isDark(r, c) {
      return qr.isDark(r, c);
    },
  };
}

/**
 * Code128 模块串 + 静默区。
 * @param {string} text 券码
 * @returns {string} '1'=黑条 '0'=空白
 */
function encodeBars(text) {
  const bits = code128.encode(text);
  if (!bits) return '';
  return '0'.repeat(BAR_QUIET_MODULES) + bits + '0'.repeat(BAR_QUIET_MODULES);
}

/**
 * 画二维码。尺寸不足时返回 false（调用方据此退回纯文本券码，不画糊码）。
 * @returns {boolean} 是否绘制成功
 */
function drawQr(ctx, text, size, opts) {
  const o = opts || {};
  const matrix = buildQrMatrix(text);
  const total = matrix.count + QR_QUIET_MODULES * 2;
  const module = Math.floor(size / total);
  if (module < 2) return false; // 每模块不足 2px，扫码枪读不出，宁可不画
  const drawn = module * total;
  const left = Math.floor((size - drawn) / 2);
  const top = Math.floor((size - drawn) / 2);
  ctx.fillStyle = o.paper || PAPER;
  ctx.fillRect(left, top, drawn, drawn);
  ctx.fillStyle = o.ink || INK;
  for (let r = 0; r < matrix.count; r += 1) {
    for (let c = 0; c < matrix.count; c += 1) {
      if (!matrix.isDark(r, c)) continue;
      ctx.fillRect(
        left + (c + QR_QUIET_MODULES) * module,
        top + (r + QR_QUIET_MODULES) * module,
        module,
        module
      );
    }
  }
  return true;
}

/**
 * 画 Code128 条形码。尺寸不足时返回 false。
 * 券码 16 字符 → 211 模块 + 两侧静默区共 231 模块，屏宽只够 1 CSS px/模块
 * （dpr 2~3 ⇒ 2~3 设备像素/模块，二维影像枪近距可读）。所以这里的下限是 1 而不是 2：
 * 下限 2 会让条形码在手机上永远画不出来，备通道形同虚设。
 * @returns {boolean} 是否绘制成功
 */
function drawBarcode(ctx, text, width, height, opts) {
  const o = opts || {};
  const bits = encodeBars(text);
  if (!bits) return false;
  const module = Math.floor(width / bits.length);
  if (module < 1) return false;
  const drawn = module * bits.length;
  const left = Math.floor((width - drawn) / 2);
  ctx.fillStyle = o.paper || PAPER;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = o.ink || INK;
  let runStart = -1;
  for (let i = 0; i <= bits.length; i += 1) {
    const isBar = i < bits.length && bits[i] === '1';
    if (isBar && runStart < 0) runStart = i;
    if (!isBar && runStart >= 0) {
      ctx.fillRect(left + runStart * module, 0, (i - runStart) * module, height);
      runStart = -1;
    }
  }
  return true;
}

module.exports = { buildQrMatrix, encodeBars, drawQr, drawBarcode, INK, PAPER };
