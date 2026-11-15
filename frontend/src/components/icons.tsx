/** SF 风格内联线性图标集：1.8px 圆帽描边，currentColor 着色，替换 emoji 字形。 */

import type { CSSProperties, ReactNode } from "react";

export type IconName =
  | "cursor"
  | "hand"
  | "ruler"
  | "eyedropper"
  | "grid"
  | "undo"
  | "redo"
  | "import"
  | "fit"
  | "sun"
  | "moon"
  | "chevron-down"
  | "eye"
  | "eye-off"
  | "lock"
  | "unlock"
  | "arrow-up"
  | "arrow-down"
  | "close"
  | "play"
  | "check"
  | "warn"
  | "search"
  | "refresh"
  | "share"
  | "layers"
  | "sliders"
  | "box"
  | "flask";

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

/** stroke 类图标路径（1.8px 圆帽）。 */
const STROKE_PATHS: Record<Exclude<IconName, "cursor">, ReactNode> = {
  hand: (
    <>
      <path d="M8 12V5.6a1.3 1.3 0 0 1 2.6 0V11" />
      <path d="M10.6 11V4.6a1.3 1.3 0 0 1 2.6 0V11" />
      <path d="M13.2 11V5.6a1.3 1.3 0 0 1 2.6 0V12" />
      <path d="M15.8 12.5V8.4a1.3 1.3 0 0 1 2.6 0v5.6a7 7 0 0 1-7 7h-.7a6.3 6.3 0 0 1-5.3-2.9l-1.7-2.7c-.8-1.3.8-2.6 2-1.7l1.7 1.3V12" />
    </>
  ),
  ruler: (
    <>
      <path d="M3.5 16.5 16.5 3.5l4 4L7.5 20.5z" />
      <path d="m7.5 12.5 2 2" />
      <path d="m10.5 9.5 2 2" />
      <path d="m13.5 6.5 2 2" />
    </>
  ),
  eyedropper: (
    <>
      <path d="m14.5 5.5 4 4" />
      <path d="M4 20v-3.6L13.8 6.6a2.1 2.1 0 0 1 3 3L7 19.4l-3 .6z" />
      <path d="m16.5 3.5 1.4-1.4a1.7 1.7 0 0 1 2.4 0l1.6 1.6a1.7 1.7 0 0 1 0 2.4L20.5 7.5" />
    </>
  ),
  grid: (
    <>
      <rect x="3.5" y="3.5" width="17" height="17" rx="2.5" />
      <path d="M3.5 9.2h17M3.5 14.8h17M9.2 3.5v17M14.8 3.5v17" />
    </>
  ),
  undo: (
    <>
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h9.5a6.5 6.5 0 0 1 0 13H10" />
    </>
  ),
  redo: (
    <>
      <path d="m15 14 5-5-5-5" />
      <path d="M20 9h-9.5a6.5 6.5 0 0 0 0 13H14" />
    </>
  ),
  import: (
    <>
      <path d="M12 3v10" />
      <path d="m8 9.5 4 4 4-4" />
      <path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15" />
    </>
  ),
  fit: (
    <>
      <path d="M9 3.5H3.5V9" />
      <path d="M15 3.5h5.5V9" />
      <path d="M9 20.5H3.5V15" />
      <path d="M15 20.5h5.5V15" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6" />
    </>
  ),
  moon: <path d="M20 13A8.1 8.1 0 1 1 11 4a6.6 6.6 0 0 0 9 9z" />,
  "chevron-down": <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />,
  eye: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  "eye-off": (
    <>
      <path d="M4 4l16 16" />
      <path d="M9.9 5.9A9.8 9.8 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17.6 17.6 0 0 1-2.8 3.6M6.6 6.6A16.9 16.9 0 0 0 2.5 12S6 18.5 12 18.5a9.7 9.7 0 0 0 4.3-1" />
      <path d="M9.9 9.9a2.9 2.9 0 0 0 4.1 4.1" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2.5" />
      <path d="M8 10.5V8a4 4 0 0 1 7.8-1.2" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2.5" />
      <path d="M8 10.5V8a4 4 0 0 1 7.5-1.9" />
    </>
  ),
  "arrow-up": <path d="M12 19V5m0 0-5.5 5.5M12 5l5.5 5.5" />,
  "arrow-down": <path d="M12 5v14m0 0 5.5-5.5M12 19l-5.5-5.5" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  play: <path d="M7 4.8v14.4a.6.6 0 0 0 .9.5l11.5-7.2a.6.6 0 0 0 0-1L7.9 4.3a.6.6 0 0 0-.9.5z" />,
  check: <path d="m4.5 12.5 5 5 10-11" />,
  warn: (
    <>
      <path d="M12 3.5 2.5 20h19L12 3.5z" />
      <path d="M12 9.5v4.5" />
      <path d="M12 17.2h.01" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.5 15.5 5 5" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 3.5V8h-4.5" />
    </>
  ),
  share: (
    <>
      <path d="M12 3.5v11" />
      <path d="m8 7 4-3.5L16 7" />
      <path d="M5.5 11v8a1.5 1.5 0 0 0 1.5 1.5h10a1.5 1.5 0 0 0 1.5-1.5v-8" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3.5 8.5 4.5L12 12.5 3.5 8z" />
      <path d="m3.5 12.5 8.5 4.5 8.5-4.5" />
      <path d="m3.5 16.5 8.5 4.5 8.5-4.5" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 8h10M18 8h2M4 16h4M12 16h8" />
      <circle cx="16" cy="8" r="2" />
      <circle cx="10" cy="16" r="2" />
    </>
  ),
  box: (
    <>
      <path d="M3.5 8 12 3.5 20.5 8v8L12 20.5 3.5 16z" />
      <path d="M3.5 8 12 12.5 20.5 8" />
      <path d="M12 12.5v8" />
    </>
  ),
  flask: (
    <>
      <path d="M9.5 3.5h5" />
      <path d="M10.5 3.5v5L4.8 18a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3l-5.7-9.5v-5" />
      <path d="M7.5 14.5h9" />
    </>
  ),
};

/** fill 类图标（光标箭头）。 */
const FILL_PATHS: Record<"cursor", ReactNode> = {
  cursor: (
    <path d="M5.6 3.4c-.5-.5-1.3-.1-1.2.6l3.2 15.7c.1.7 1 .9 1.4.3l3.3-5.3 5.9-1.2c.7-.2.9-1 .3-1.4L5.6 3.4z" />
  ),
};

export function Icon({ name, size = 16, className, style }: IconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    className,
    style,
    "aria-hidden": true as const,
  };
  if (name === "cursor") {
    return (
      <svg {...common} fill="currentColor">
        {FILL_PATHS.cursor}
      </svg>
    );
  }
  return (
    <svg
      {...common}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {STROKE_PATHS[name]}
    </svg>
  );
}
