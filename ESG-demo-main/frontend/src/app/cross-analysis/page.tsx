"use client";

import React, { useEffect, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getDefaultDimensionKey } from "@/data/crossTaxonomy";
import { useT } from "@/i18n/useT";

/**
 * Cross Analysis entry route.
 */
export default function CrossAnalysisEntryPage() {
  const { t } = useT();
  const router = useRouter();
  const searchParams = useSearchParams();

  const qsString = useMemo(() => searchParams.toString(), [searchParams]);

  useEffect(() => {
    router.replace(`/cross-analysis/${getDefaultDimensionKey()}${qsString ? `?${qsString}` : ""}`);
  }, [qsString, router]);

  return (
    <div className="min-h-[60vh] w-full flex items-center justify-center text-slate-500">
      {t("crossAnalysis.title")}
    </div>
  );
}
