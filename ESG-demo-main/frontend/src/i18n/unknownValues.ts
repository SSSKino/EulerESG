import { DICT } from "./dict";

/** Backend placeholder values that mean "unknown" across locales. */
const UNKNOWN_PLACEHOLDER_VALUES = new Set<string>([
  DICT.en.common.unknown,
  DICT.zh.common.unknown,
]);

export function isUnknownPlaceholder(value: unknown): boolean {
  if (value == null || value === "") return true;
  return UNKNOWN_PLACEHOLDER_VALUES.has(String(value));
}
