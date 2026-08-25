import { Empty } from "antd";

type Character =
  | "bookworm"
  | "star"
  | "bear"
  | "rabbit"
  | "cat"
  | "rainbow"
  | "bird"
  | "default";

const CHARACTER_SVGS: Record<Character, JSX.Element> = {
  bookworm: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <circle cx="60" cy="70" r="38" fill="#4ADE80" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="48" cy="62" r="6" fill="#3B2F2F" />
      <circle cx="72" cy="62" r="6" fill="#3B2F2F" />
      <path d="M52 80 Q60 88 68 80" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <rect x="75" y="55" width="22" height="30" rx="4" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <path d="M80 62 H92 M80 70 H92 M80 78 H92" stroke="#FFFDF7" strokeWidth="2" strokeLinecap="round" />
      <path d="M35 95 Q30 108 45 108" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <path d="M85 95 Q90 108 75 108" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  star: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <polygon points="60,15 72,45 105,45 78,65 88,95 60,75 32,95 42,65 15,45 48,45" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="52" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="52" r="5" fill="#3B2F2F" />
      <path d="M52 64 Q60 70 68 64" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <path d="M45 30 L40 20 M75 30 L80 20" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  bear: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <circle cx="38" cy="38" r="14" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="82" cy="38" r="14" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="68" r="34" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <ellipse cx="60" cy="76" rx="12" ry="10" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="2" />
      <circle cx="50" cy="62" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="62" r="5" fill="#3B2F2F" />
      <ellipse cx="60" cy="70" rx="4" ry="3" fill="#3B2F2F" />
      <path d="M54 82 Q60 86 66 82" fill="none" stroke="#3B2F2F" strokeWidth="2" strokeLinecap="round" />
      <rect x="75" y="88" width="22" height="18" rx="4" fill="#60A5FA" stroke="#3B2F2F" strokeWidth="2" />
    </svg>
  ),
  rabbit: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <ellipse cx="45" cy="35" rx="10" ry="28" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <ellipse cx="75" cy="35" rx="10" ry="28" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="75" r="32" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="70" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="70" r="5" fill="#3B2F2F" />
      <path d="M55 82 Q60 86 65 82" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <polygon points="60,45 68,60 52,60" fill="#F472B6" stroke="#3B2F2F" strokeWidth="2" />
      <rect x="42" y="98" width="36" height="14" rx="7" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="2" />
    </svg>
  ),
  cat: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <polygon points="35,55 25,25 50,45" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <polygon points="85,55 95,25 70,45" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="70" r="34" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="65" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="65" r="5" fill="#3B2F2F" />
      <path d="M55 78 Q60 82 65 78" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <circle cx="85" cy="85" r="12" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="2" />
      <text x="80" y="90" fontSize="10" fill="#3B2F2F" fontWeight="700">$</text>
    </svg>
  ),
  rainbow: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <path d="M20 90 Q20 30 60 30 Q100 30 100 90" fill="none" stroke="#EF4444" strokeWidth="8" strokeLinecap="round" />
      <path d="M30 90 Q30 45 60 45 Q90 45 90 90" fill="none" stroke="#FCD34D" strokeWidth="8" strokeLinecap="round" />
      <path d="M40 90 Q40 60 60 60 Q80 60 80 90" fill="none" stroke="#4ADE80" strokeWidth="8" strokeLinecap="round" />
      <ellipse cx="30" cy="95" rx="12" ry="8" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <ellipse cx="90" cy="95" rx="12" ry="8" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
    </svg>
  ),
  bird: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <ellipse cx="60" cy="65" rx="32" ry="28" fill="#60A5FA" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="75" cy="55" r="5" fill="#3B2F2F" />
      <polygon points="85,58 100,52 85,66" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="2" />
      <path d="M55 70 Q60 74 65 70" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <path d="M35 60 Q25 50 30 40" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <path d="M28 85 Q40 95 55 88" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  default: (
    <svg viewBox="0 0 120 120" width="100" height="100" aria-hidden>
      <circle cx="60" cy="60" r="40" fill="#FFF5E6" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="48" cy="55" r="5" fill="#3B2F2F" />
      <circle cx="72" cy="55" r="5" fill="#3B2F2F" />
      <path d="M50 72 Q60 80 70 72" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
};

interface PaintEmptyProps {
  character?: Character;
  message?: string;
  description?: string;
}

export default function PaintEmpty({
  character = "default",
  message = "这里空空如也～",
  description,
}: PaintEmptyProps) {
  return (
    <Empty
      image={CHARACTER_SVGS[character]}
      description={
        <div style={{ textAlign: "center" }}>
          <div style={{ fontFamily: "'ZCOOL KuaiLe', 'Nunito', sans-serif", fontSize: 16, color: "#3B2F2F" }}>
            {message}
          </div>
          {description && (
            <div style={{ fontSize: 13, color: "#6B5B5B", marginTop: 4 }}>{description}</div>
          )}
        </div>
      }
    />
  );
}

export { CHARACTER_SVGS };
export type { Character };
