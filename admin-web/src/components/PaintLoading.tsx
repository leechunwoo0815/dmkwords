import { Spin } from "antd";

type Character = "bookworm" | "star" | "bear" | "rabbit" | "cat" | "rainbow" | "bird";

const LOADING_SVGS: Record<Character, JSX.Element> = {
  bookworm: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <circle cx="60" cy="70" r="38" fill="#4ADE80" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="48" cy="62" r="6" fill="#3B2F2F" />
      <circle cx="72" cy="62" r="6" fill="#3B2F2F" />
      <path d="M52 80 Q60 88 68 80" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
      <rect x="75" y="55" width="22" height="30" rx="4" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <path d="M80 62 H92 M80 70 H92 M80 78 H92" stroke="#FFFDF7" strokeWidth="2" strokeLinecap="round" />
    </svg>
  ),
  star: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <polygon points="60,15 72,45 105,45 78,65 88,95 60,75 32,95 42,65 15,45 48,45" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="52" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="52" r="5" fill="#3B2F2F" />
      <path d="M52 64 Q60 70 68 64" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  bear: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <circle cx="38" cy="38" r="14" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="82" cy="38" r="14" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="68" r="34" fill="#8B5E3C" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="62" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="62" r="5" fill="#3B2F2F" />
      <path d="M54 82 Q60 86 66 82" fill="none" stroke="#3B2F2F" strokeWidth="2" strokeLinecap="round" />
    </svg>
  ),
  rabbit: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <ellipse cx="45" cy="35" rx="10" ry="28" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <ellipse cx="75" cy="35" rx="10" ry="28" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="75" r="32" fill="#FFF" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="70" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="70" r="5" fill="#3B2F2F" />
      <path d="M55 82 Q60 86 65 82" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  cat: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <polygon points="35,55 25,25 50,45" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <polygon points="85,55 95,25 70,45" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="60" cy="70" r="34" fill="#FF6B35" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="50" cy="65" r="5" fill="#3B2F2F" />
      <circle cx="70" cy="65" r="5" fill="#3B2F2F" />
      <path d="M55 78 Q60 82 65 78" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
  rainbow: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <path d="M20 90 Q20 30 60 30 Q100 30 100 90" fill="none" stroke="#EF4444" strokeWidth="8" strokeLinecap="round" />
      <path d="M30 90 Q30 45 60 45 Q90 45 90 90" fill="none" stroke="#FCD34D" strokeWidth="8" strokeLinecap="round" />
      <path d="M40 90 Q40 60 60 60 Q80 60 80 90" fill="none" stroke="#4ADE80" strokeWidth="8" strokeLinecap="round" />
    </svg>
  ),
  bird: (
    <svg viewBox="0 0 120 120" width="80" height="80" aria-hidden className="paint-character-idle">
      <ellipse cx="60" cy="65" rx="32" ry="28" fill="#60A5FA" stroke="#3B2F2F" strokeWidth="3" />
      <circle cx="75" cy="55" r="5" fill="#3B2F2F" />
      <polygon points="85,58 100,52 85,66" fill="#FCD34D" stroke="#3B2F2F" strokeWidth="2" />
      <path d="M55 70 Q60 74 65 70" fill="none" stroke="#3B2F2F" strokeWidth="3" strokeLinecap="round" />
    </svg>
  ),
};

interface PaintLoadingProps {
  character?: Character;
  message?: string;
}

export default function PaintLoading({ character = "bookworm", message = "正在加载..." }: PaintLoadingProps) {
  return (
    <div style={{ textAlign: "center", padding: "40px 0" }}>
      <Spin indicator={LOADING_SVGS[character]} tip={message} />
    </div>
  );
}
