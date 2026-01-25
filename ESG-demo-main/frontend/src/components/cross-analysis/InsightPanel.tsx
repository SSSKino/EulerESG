"use client";

import React from "react";
import { Skeleton } from "antd";
import type { CrossCompareResponse } from "@/lib/api";

export default function InsightPanel({
  loading,
  compare,
}: {
  loading: boolean;
  compare: CrossCompareResponse | null;
}) {
  if (loading) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white/60 p-4 shadow-sm backdrop-blur">
      <div className="text-sm font-semibold text-slate-900">Difference insight</div>
      <div className="mt-1 text-xs text-slate-500">
        Interpreted under a consistent taxonomy; treat as directional unless reporting boundaries are harmonized.
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">
        {compare?.insight || "No cross-report insight available for this topic yet."}
      </div>
    </div>
  );
}
