import { en, type MessageKey } from "./en";

/** Minimal i18n scaffold: English only. Unknown keys fall back to the key itself. */
export function t(key: MessageKey | string): string {
  return (en as Record<string, string>)[key] ?? key;
}
