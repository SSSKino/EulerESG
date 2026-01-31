"use client";

import React, { useEffect, useMemo } from "react";
import { Layout } from "antd";
import Nav from "@/components/navbar/Nav";
import CrossAnalysisDashboardRedirect from "@/components/cross-analysis/CrossAnalysisDashboardRedirect";
import { usePathname, useSearchParams } from "next/navigation";

const { Content } = Layout;

export default function CrossAnalysisLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";
  const searchParams = useSearchParams();

  const isEvidenceRoute = useMemo(() => pathname.includes("/cross-analysis/evidence"), [pathname]);
  const framework = useMemo(() => (searchParams.get("framework") || "").trim(), [searchParams]);

  // Evidence view should behave like a standalone reader page:
  // - no nested flex containers
  // - browser handles scrolling (no extra scroll frame)
  // - allows wide content when user zooms in
  if (isEvidenceRoute) {
    return <>{children}</>;
  }

  // If framework is missing, redirect to dashboard and launch the picker there.
  // Return null so no cross-analysis layout is shown as the background.
  if (!isEvidenceRoute && !framework) {
    return <CrossAnalysisDashboardRedirect>{null}</CrossAnalysisDashboardRedirect>;
  }

  // Reuse the same root layout fade-in behavior as dashboard for a consistent feel.
  useEffect(() => {
    const root = document.querySelector(".root-layout");
    if (!root) return;
    const timer = setTimeout(() => root.classList.add("loaded"), 100);
    return () => {
      clearTimeout(timer);
      root.classList.remove("loaded");
    };
  }, []);

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Nav />
      <Content style={{ display: "flex" }}>
        {children}
      </Content>
    </Layout>
  );
}
