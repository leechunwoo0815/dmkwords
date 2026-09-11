import { Empty } from "antd";

import manifest from "../constants/avatars.json";

/**
 * WM15-R3：系统内置头像选择器（24 枚 = 12 动物 × 2 配色）。
 *
 * 清单来自 `src/constants/avatars.json`（**生成物**，源 = 后端 art.AVATAR_IDS；
 * 由 scripts/gen_wm15_visuals.py 产出，test_wm15_assets 会与后端常量对拍）。
 * 图片走 admin-web/public/avatars/ 静态资源，零 token。
 *
 * 用法：`<Form.Item name="avatar" label="头像"><AvatarPicker /></Form.Item>`
 * （antd Form.Item 自动注入 value/onChange）
 */
type Props = {
  value?: string;
  onChange?: (v: string) => void;
  disabled?: boolean;
};

export default function AvatarPicker({ value, onChange, disabled }: Props) {
  const ids: string[] = manifest.avatars ?? [];
  if (!ids.length) return <Empty description="头像库未生成" />;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {ids.map((id) => {
        const active = value === id;
        return (
          <button
            key={id}
            type="button"
            title={id}
            disabled={disabled}
            onClick={() => onChange?.(active ? "" : id)}
            style={{
              width: 44,
              height: 44,
              padding: 0,
              borderRadius: "50%",
              cursor: disabled ? "not-allowed" : "pointer",
              background: "#fff",
              border: active ? "3px solid #F2935B" : "2px solid rgba(90,74,58,.18)",
              lineHeight: 0,
            }}
          >
            <img
              src={`/avatars/${id}.png`}
              alt={id}
              width={36}
              height={36}
              style={{ borderRadius: "50%" }}
            />
          </button>
        );
      })}
    </div>
  );
}
