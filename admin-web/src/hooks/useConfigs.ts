// 配置读取钩子 —— 价格/规则文案一律来自后端 SystemConfig。
//
// 宪法 §五.4：价格、规则文案一律后端配置下发，禁止硬编码金额（含兜底文案）。
// E-20260912-10（2026-09-12 全维度审查）：此前 admin-web 有 7 处把金额写死在文案里
// （订单类型下拉「观察期会员费（500 元/月）」「正式年费（6000 元，二孩自动 5400）」等），
// 调价后界面文案不会跟着变 → 统一改走本钩子。
import { useEffect, useState } from "react";

import { apiListConfigs } from "../api/admin";

export type ConfigMap = Record<string, string>;

// 进程内缓存：配置不常变，避免每个页面各拉一次。
//
// P2-55（2026-10-20 第八轮）：原先 **cache 永不失效**——超管在配置中心改完价格，
// 其他页面（会员管理/押金管理）仍显示旧金额，直到整页刷新才跟上（"改价后文案不跟随"）。
// 现在两条出路：① 配置中心保存成功即 `invalidateConfigsCache()`；② 兜底 TTL（5 分钟）。
const CONFIGS_TTL_MS = 5 * 60 * 1000;
let cache: ConfigMap | null = null;
let cachedAt = 0;

/** 清配置缓存（配置中心保存后调用；也可用于"改完立刻生效"的场景）。 */
export function invalidateConfigsCache(): void {
  cache = null;
  cachedAt = 0;
}

export interface ConfigsState {
  configs: ConfigMap;
  /**
   * 配置**没取到**（网络/权限失败）→ true。此时页面上的金额是按 `fallback` 渲染的**默认值**，
   * 与后端实际配置可能不一致——页面必须**如实提示**，不许让运营照默认值办事（P2-55）。
   */
  failed: boolean;
  reload: () => void;
}

export function useConfigs(): ConfigsState {
  const fresh = cache !== null && Date.now() - cachedAt < CONFIGS_TTL_MS;
  const [configs, setConfigs] = useState<ConfigMap>(fresh ? (cache as ConfigMap) : {});
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (cache !== null && Date.now() - cachedAt < CONFIGS_TTL_MS) {
      setConfigs(cache);
      setFailed(false);
      return;
    }
    let alive = true;
    apiListConfigs()
      .then((rows) => {
        const map: ConfigMap = {};
        rows.forEach((r) => {
          map[r.config_key] = r.config_value;
        });
        cache = map;
        cachedAt = Date.now();
        if (alive) {
          setConfigs(map);
          setFailed(false);
        }
      })
      .catch(() => {
        // P2-55：**不再静默**——置 failed，页面据此提示"金额为默认值，可能不准"
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  return { configs, failed, reload: () => setTick((n) => n + 1) };
}

/** 取数字型配置（缺失/非法时回退 fallback，保证首帧文案不塌） */
export function cfgNum(configs: ConfigMap, key: string, fallback: number): number {
  const raw = configs[key];
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

/** 千分位金额（文案统一格式：6,000） */
export function money(n: number): string {
  return n.toLocaleString("zh-CN");
}
