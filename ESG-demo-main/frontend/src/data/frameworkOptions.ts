/** Frameworks available when creating a new report or cross-analysis. */
export const ACTIVE_FRAMEWORK_OPTIONS = [
  { label: "SASB", value: "SASB" },
  { label: "GRI", value: "GRI" },
  { label: "CDP", value: "CDP" },
];

export function isActiveFramework(value: unknown): boolean {
  const normalized = String(value ?? "").trim().toUpperCase();
  return ACTIVE_FRAMEWORK_OPTIONS.some((option) => option.value === normalized);
}
