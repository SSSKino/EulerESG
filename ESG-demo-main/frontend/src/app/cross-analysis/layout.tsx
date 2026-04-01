"use client";

import React, { useEffect, useMemo } from "react";
import { Layout } from "antd";
import Nav from "@/components/navbar/Nav";
import { usePathname } from "next/navigation";

const { Content } = Layout;

export default function CrossAnalysisLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";

  const isEvidenceRoute = useMemo(() => pathname.includes("/cross-analysis/evidence"), [pathname]);

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

  // Evidence view should behave like a standalone reader page:
  // - no nested flex containers
  // - browser handles scrolling (no extra scroll frame)
  // - allows wide content when user zooms in
  if (isEvidenceRoute) {
    return <>{children}</>;
  }

  // Cross Analysis now uses per-report assessment outputs directly;
  // no extra framework selection gate is required.

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Nav />
      <Content style={{ display: "flex" }}>
        {children}
      </Content>
    </Layout>
  );
}
