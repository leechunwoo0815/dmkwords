// 配置读取钩子 —— 价格/规则文案一律来自后端 SystemConfig。
//
// 宪法 §五.4：价格、规则文案一律后端配置下发，禁止硬编码金额（含兜底文案）。
// E-20260912-10（2026-09-12 全维度审查）：此前 admin-web 有 7 处把金额写死在文案里
// （订单类型下拉「观察期会员费（500 元/月）」「正式年费（6000 元，二孩自动 5400）」等），
// 调价后界面文案不会跟着变 → 统一改走本钩子。
import { useEffect, useState } from "react";

import { apiListConfigs } from "../api/admin";

export type ConfigMap = Record<string, string>;

// 进程内缓存：配置不常变，避免每个页面各拉一次（登出/权限变更场景由整页刷新兜住）
let cache: ConfigMap | null = null;

export function useConfigs(): ConfigMap {
  const [configs, setConfigs] = useState<ConfigMap>(cache ?? {});
  useEffect(() => {
    if (cache !== null) return;
    let alive = true;
    apiListConfigs()
      .then((rows) => {
        const map: ConfigMap = {};
        rows.forEach((r) => {
          map[r.config_key] = r.config_value;
        });
        cache = map;
        if (alive) setConfigs(map);
      })
      .catch(() => {
        /* request.js 已统一提示；缺配置时用 fallback 渲染，不阻断页面 */
      });
    return () => {
      alive = false;
    };
  }, []);
  return configs;
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
