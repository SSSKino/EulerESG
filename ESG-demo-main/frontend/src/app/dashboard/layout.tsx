"use client";

import React, { useEffect } from "react";
import AppShell from "@/components/app/AppShell";
import { useRouter } from "next/navigation";
import { AUTH_TOKEN_KEY } from "@/lib/auth";

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

  return <AppShell>{children}</AppShell>;
}
