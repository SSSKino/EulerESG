"use client";

import React, { useEffect } from "react";
import { Layout } from "antd";
import Nav from "@/components/navbar/Nav";

const { Content } = Layout;

export default function CrossAnalysisLayout({ children }: { children: React.ReactNode }) {
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
      <Content style={{ display: "flex" }}>{children}</Content>
    </Layout>
  );
}
