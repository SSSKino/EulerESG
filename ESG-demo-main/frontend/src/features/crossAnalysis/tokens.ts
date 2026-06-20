// Visual tokens aligned with globals.css --brand-* variables.

export const crossTokens = {
  color: {
    bg: "var(--brand-surface)",
    card: "#FFFFFF",
    cardAlt: "rgb(255 255 255 / 0.92)",
    border: "rgb(0 0 0 / 0.06)",
    text: "var(--brand-text)",
    subtext: "var(--brand-subtle)",
    accent: "var(--brand-accent)",
    accentSoft: "var(--brand-accent-soft)",
    primary: "var(--brand-primary)",
    primarySoft: "var(--brand-primary-soft)",
  },
  radius: {
    card: 16,
    pill: 999,
  },
  shadow: {
    card: "0 8px 24px rgb(0 0 0 / 0.05)",
    subtle: "0 2px 10px rgb(0 0 0 / 0.05)",
  },
  spacing: {
    xs: 8,
    sm: 12,
    md: 16,
    lg: 24,
    xl: 32,
  },
  motion: {
    hoverMs: 140,
    panelMs: 220,
  },
};

/** Chart series colors — green-first palette aligned with Euler ESG brand. */
export const CHART_PALETTE = [
  "#1b6b4a",
  "#3d8f6e",
  "#6aaf8f",
  "#5a9fd4",
  "#8bb8dc",
  "#c9a227",
  "#8b6bae",
  "#d4739a",
] as const;

export const DEFAULT_CHART_COLOR = CHART_PALETTE[0];

export function chartColorAt(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length];
}
