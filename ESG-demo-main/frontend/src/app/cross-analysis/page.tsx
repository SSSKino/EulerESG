"use client";

import React, { useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getDefaultDimensionKey } from "@/data/crossTaxonomy";

/**
 * Cross Analysis entry route.
 *
 * Framework selection is enforced by the Cross Analysis layout, which redirects
 * to the dashboard and launches the framework picker there (dashboard is the background).
 *
 * This page simply redirects to the default dimension once the framework exists.
 */
export default function CrossAnalysisEntryPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const qsString = useMemo(() => searchParams.toString(), [searchParams]);
  const framework = useMemo(() => (searchParams.get("framework") || "").trim(), [searchParams]);

  useEffect(() => {
    if (!framework) return;
    router.replace(`/cross-analysis/${getDefaultDimensionKey()}${qsString ? `?${qsString}` : ""}`);
  }, [framework, qsString, router]);

  return (
    <div className="min-h-[60vh] w-full flex items-center justify-center text-slate-500">
      Cross Analysis
    </div>
  );
}
