"use client";

import React, { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getDefaultDimensionKey } from "@/data/crossTaxonomy";

/**
 * Entry route for Cross Analysis (beta).
 * The new UX uses per-dimension pages:
 *   /cross-analysis/environment?... etc
 */
export default function CrossAnalysisEntryPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const ids = searchParams.get("ids");
    const qs = ids ? `?ids=${encodeURIComponent(ids)}` : "";
    router.replace(`/cross-analysis/${getDefaultDimensionKey()}${qs}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
