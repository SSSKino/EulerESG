"use client";

import { useAppLang } from "@/i18n/useAppLang";
import { useT } from "@/i18n/useT";

export default function LangToggle() {
  const { lang, setLang } = useAppLang();
  const { t } = useT();

  return (
    <div className="app-lang-toggle flex h-9 items-center rounded-full border border-black/8 bg-white/80 px-0.5 shadow-[0_1px_3px_rgba(0,0,0,0.05)]">
      <button
        type="button"
        onClick={() => setLang("zh")}
        className={`h-7 min-w-[2rem] rounded-full px-1.5 text-[15px] font-medium transition-colors ${
          lang === "zh" ? "bg-[var(--brand-primary)] text-white" : "text-[var(--brand-muted)] hover:bg-black/5"
        }`}
        aria-pressed={lang === "zh"}
      >
        {t("common.langZh")}
      </button>
      <button
        type="button"
        onClick={() => setLang("en")}
        className={`h-7 min-w-[2rem] rounded-full px-1.5 text-[13px] font-medium transition-colors ${
          lang === "en" ? "bg-[var(--brand-primary)] text-white" : "text-[var(--brand-muted)] hover:bg-black/5"
        }`}
        aria-pressed={lang === "en"}
      >
        {t("common.langEn")}
      </button>
    </div>
  );
}
