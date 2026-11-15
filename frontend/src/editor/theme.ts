/** 画布主题调色板：与 tokens.css 的 --well 等值保持一致（两处必须同步修改）。
 *  纸面恒白（印刷品本身），刀线行业色固定；仅井底/标尺/度量提示随主题切换。 */

export type ThemeName = "dark" | "light";

export interface StagePalette {
  /** 画布井底（与 tokens.css --well 相同值） */
  well: string;
  /** 纸张填充（两主题恒白） */
  sheetFill: string;
  /** 纸张描边（井底上勾勒纸边） */
  sheetBorder: string;
  /** 纸面网格（只画在纸张盒内） */
  grid: string;
  /** 参考线/选中框/度量线的强调色 */
  accent: string;
  /** 度量提示气泡底 */
  measureBg: string;
  /** 度量提示气泡文字 */
  measureText: string;
  /** 标尺刻度线（玻璃标尺上） */
  rulerTick: string;
  /** 标尺数字（玻璃标尺上） */
  rulerText: string;
}

export const CANVAS_PALETTES: Record<ThemeName, StagePalette> = {
  dark: {
    well: "#1b1b1d",
    sheetFill: "#ffffff",
    sheetBorder: "#48484a",
    grid: "#f0f0f3",
    accent: "#0a84ff",
    measureBg: "rgba(250, 250, 252, 0.92)",
    measureText: "#1c1c1e",
    rulerTick: "rgba(255, 255, 255, 0.30)",
    rulerText: "rgba(235, 235, 245, 0.60)",
  },
  light: {
    well: "#ececf1",
    sheetFill: "#ffffff",
    sheetBorder: "#d1d1d6",
    grid: "#f0f0f3",
    accent: "#007aff",
    measureBg: "rgba(28, 28, 30, 0.85)",
    measureText: "#ffffff",
    rulerTick: "rgba(60, 60, 67, 0.24)",
    rulerText: "rgba(60, 60, 67, 0.62)",
  },
};

export function getPalette(theme: ThemeName): StagePalette {
  return CANVAS_PALETTES[theme] ?? CANVAS_PALETTES.dark;
}
