"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import { Layout } from "antd";
import DashboardSidebar from "@/components/navbar/DashboardSidebar";
import { CrossAnalysisNavigationSlotContext } from "@/components/cross-analysis/CrossAnalysisNavigationPortal";
import { usePathname } from "next/navigation";
import { AntdRegistry } from "@/lib/antd";

const { Content } = Layout;

export default function CrossAnalysisLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "";
  const [navigationSlot, setNavigationSlot] = useState<HTMLElement | null>(null);

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
    return <AntdRegistry>{children}</AntdRegistry>;
  }

  // Cross Analysis now uses per-report assessment outputs directly;
  // no extra framework selection gate is required.

  return (
    <AntdRegistry>
      <CrossAnalysisNavigationSlotContext.Provider value={navigationSlot}>
        <Layout
          data-dashboard-shell
          style={{ minHeight: "100vh", flexDirection: "row" }}
        >
          <Suspense
            fallback={<div aria-hidden="true" className="h-screen w-[260px] shrink-0 bg-[#f9f9f9]" />}
          >
            <DashboardSidebar crossAnalysisNavigationSlotRef={setNavigationSlot} />
          </Suspense>
          <Content style={{ display: "flex", minWidth: 0 }}>
            {children}
          </Content>
        </Layout>
      </CrossAnalysisNavigationSlotContext.Provider>
    </AntdRegistry>
  );
}
