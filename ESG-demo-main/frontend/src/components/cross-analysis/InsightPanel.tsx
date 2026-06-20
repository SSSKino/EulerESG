"use client";

import React from "react";
import { useT } from "@/i18n/useT";
import { Skeleton } from "antd";
import type { CrossCompareResponse } from "@/lib/api";

export default function InsightPanel({
  loading,
  compare,
}: {
  loading: boolean;
  compare: CrossCompareResponse | null;
}) {
  const { t } = useT();
  if (loading) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }

  return (
    <div className="app-card p-4">
      <div className="text-sm font-semibold text-[var(--brand-text)]">{t("crossAnalysis.insight.title")}</div>
      <div className="mt-1 text-xs text-[var(--brand-subtle)]">
        {t("crossAnalysis.insight.subtitle")}
      </div>
      <div className="mt-3 text-sm leading-relaxed text-[var(--brand-muted)]">
        {compare?.insight || t("crossAnalysis.insight.empty")}
      </div>
    </div>
  );
}
