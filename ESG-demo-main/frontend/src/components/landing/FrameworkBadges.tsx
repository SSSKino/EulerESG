"use client";

import { useT } from "@/i18n/useT";

const FRAMEWORK_NAMES = ["SASB", "GRI", "CDP", "TCFD"] as const;

type FrameworkBadgesProps = {
  className?: string;
};

export default function FrameworkBadges({ className = "" }: FrameworkBadgesProps) {
  const { t } = useT();

  return (
    <div className={`flex flex-col items-end gap-1 text-right ${className}`}>
      <span className="max-w-[9rem] text-[10px] leading-snug text-[var(--brand-subtle)] xl:max-w-none xl:text-xs">
        {t("landing.frameworksLabel")}
      </span>
      <div className="flex flex-wrap items-center justify-end gap-2.5 xl:gap-3">
        {FRAMEWORK_NAMES.map((name) => (
          <span key={name} className="text-xs font-semibold tracking-wide text-[color:rgb(28_28_28_/_0.75)] xl:text-sm">
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}
