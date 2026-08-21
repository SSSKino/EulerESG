// app/dashboard/layout.tsx
"use client";

import React, { Suspense, useEffect } from "react";
import { Layout } from "antd";
import DashboardSidebar from "@/components/navbar/DashboardSidebar";
import { useRouter } from "next/navigation";
import { AUTH_TOKEN_KEY } from "@/lib/auth";
import { AntdRegistry } from "@/lib/antd";

const { Content } = Layout;

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem(AUTH_TOKEN_KEY) : null;
    if (!token) {
      router.replace("/login");
    }
  }, [router]);

  useEffect(() => {
    const rootLayout = document.getElementById("root-layout");
    if (rootLayout) {
      const timer = setTimeout(() => {
        rootLayout.classList.add("loaded");
      }, 100);
      return () => {
        clearTimeout(timer);
        rootLayout.classList.remove("loaded");
      };
    }
  }, []);

  return (
    <AntdRegistry>
      <Layout
        data-dashboard-shell
        style={{ minHeight: "100vh", flexDirection: "row" }}
      >
        <Suspense
          fallback={<div aria-hidden="true" className="h-screen w-[260px] shrink-0 bg-[#f9f9f9]" />}
        >
          <DashboardSidebar />
        </Suspense>
        <Content style={{ display: "flex", minWidth: 0 }}>{children}</Content>
      </Layout>
    </AntdRegistry>
  );
}
