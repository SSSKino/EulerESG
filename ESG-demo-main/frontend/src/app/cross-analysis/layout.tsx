"use client";

import React, { useEffect, useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import AppShell from "@/components/app/AppShell";
import { AUTH_TOKEN_KEY } from "@/lib/auth";

export default function CrossAnalysisLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";
  const router = useRouter();

  const isEvidenceRoute = useMemo(() => pathname.includes("/cross-analysis/evidence"), [pathname]);

  useEffect(() => {
    if (isEvidenceRoute) return;
    const token = typeof window !== "undefined" ? localStorage.getItem(AUTH_TOKEN_KEY) : null;
    if (!token) {
      router.replace("/login");
    }
  }, [isEvidenceRoute, router]);

  useEffect(() => {
    if (isEvidenceRoute) return;
    const root = document.querySelector(".root-layout");
    if (!root) return;
    const timer = setTimeout(() => root.classList.add("loaded"), 100);
    return () => {
      clearTimeout(timer);
      root.classList.remove("loaded");
    };
  }, [isEvidenceRoute]);

  if (isEvidenceRoute) {
    return <>{children}</>;
  }

  return <AppShell>{children}</AppShell>;
}
