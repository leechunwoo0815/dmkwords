# tests/unit/test_miniapp_platform_isios.py — isIOS() 平台判定（2026-10-08 iOS 打桩演练抓到的缺陷）
"""缺陷（本轮实测）：`miniapp/utils/platform.js` 读 `wx.getWindowInfo().platform`，
而 **`wx.getWindowInfo()` 不返回 platform 字段**（实测 keys 只有
pixelRatio/screenWidth/screenHeight/windowWidth/windowHeight/statusBarHeight/safeArea/screenTop；
platform 属于 `wx.getDeviceInfo()` / 老 `wx.getSystemInfoSync()`）——于是 `isIOS()` 恒为 false，
**iOS 合规分支（不展示价格与「立即开通」、只给到店指引）从来没生效过**：真机 iPhone 上会照常
显示支付入口，违反微信对 iOS 虚拟支付的规定。

本测试用 node 加载**真实模块** + 6 组 wx 桩：覆盖新 API 判定、缺字段回退、老基础库回退、
异常安全侧、以及"旧实现恒 false"的根因复现。node 不可用则 skip（CI 装 node，见 ci.yml）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "miniapp" / "utils" / "platform.js"

HARNESS = """
const platform = require(__MODULE__);
const isIOS = platform.isIOS;
const stubs = {
  ios: () => ({ getDeviceInfo: () => ({ platform: 'ios' }), getSystemInfoSync: () => ({ platform: 'ios' }) }),
  android: () => ({ getDeviceInfo: () => ({ platform: 'android' }) }),
  device_without_platform: () => ({ getDeviceInfo: () => ({}), getSystemInfoSync: () => ({ platform: 'ios' }) }),
  legacy_only: () => ({ getSystemInfoSync: () => ({ platform: 'ios' }) }),
  throwing: () => ({
    getDeviceInfo: () => { throw new Error('boom'); },
    getSystemInfoSync: () => { throw new Error('boom'); },
  }),
  window_only_old_bug: () => ({ getWindowInfo: () => ({
    pixelRatio: 3, screenWidth: 390, screenHeight: 844, windowWidth: 390, windowHeight: 844,
    statusBarHeight: 47, safeArea: {}, screenTop: 0,
  }) }),
};
const out = __CASES__.map(([id, want]) => {
  globalThis.wx = stubs[id]();
  const got = isIOS();
  delete globalThis.wx;
  return { id, got, want };
});
console.log(JSON.stringify(out));
"""

#: (桩名, 期望 isIOS)
CASES = [
    ("ios", True),  # 真机 iOS：getDeviceInfo().platform = 'ios'
    ("android", False),  # 安卓不进 iOS 分支
    ("device_without_platform", True),  # 新 API 缺字段 → 回退 getSystemInfoSync
    ("legacy_only", True),  # 老基础库没有 getDeviceInfo → 回退
    ("throwing", False),  # 两个 API 都炸 → 安全侧 false
    ("window_only_old_bug", False),  # 旧实现形态：getWindowInfo 无 platform → 恒 false（根因）
]


@pytest.mark.skipif(shutil.which("node") is None, reason="本机无 node")
def test_isios_branches_with_stubbed_wx():
    script = HARNESS.replace("__MODULE__", json.dumps(str(MODULE))).replace(
        "__CASES__", json.dumps(CASES)
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, cwd=ROOT, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    failures = [r for r in results if r["got"] != r["want"]]
    assert not failures, failures
