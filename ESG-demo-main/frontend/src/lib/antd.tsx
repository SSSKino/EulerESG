"use client";

import React, { useEffect, useMemo, useState } from "react";
import { createCache, StyleProvider } from "@ant-design/cssinjs";
import { ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { useAppLang } from "@/i18n/useAppLang";
import { initLang } from "@/i18n/langStore";

const styleCache = createCache();

export function AntdRegistry({ children }: { children: React.ReactNode }) {
  const { lang } = useAppLang();

  // Prevent a visible language flip on page refresh:
  // - First render outputs nothing (SSR/CSR consistent)
  // - After mount, we read localStorage and render once with the persisted lang
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      initLang();
    } finally {
      setReady(true);
    }
  }, []);

  const locale = useMemo(() => (lang === "zh" ? zhCN : enUS), [lang]);

  useEffect(() => {
    // keep <html lang="..."> in sync for accessibility
    try {
      document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    } catch {
      // ignore
    }
  }, [lang]);

  if (!ready) {
    return <div style={{ minHeight: "100vh" }} />;
  }

  return (
    <StyleProvider cache={styleCache}>
      <ConfigProvider
        locale={locale}
        theme={{
          token: {
            colorPrimary: "#1b6b4a",
            colorInfo: "#2f7bbd",
            colorBgLayout: "#f3efe6",
            colorBgContainer: "#ffffff",
            colorBorder: "rgba(0, 0, 0, 0.06)",
            borderRadius: 12,
            borderRadiusLG: 16,
            boxShadow: "0 6px 24px rgba(0, 0, 0, 0.05)",
            fontFamily:
              "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji'",
          },
          components: {
            Button: {
              primaryShadow: "none",
              controlHeight: 40,
            },
            Table: {
              headerBg: "rgba(243, 239, 230, 0.65)",
              borderColor: "rgba(0, 0, 0, 0.06)",
            },
            Breadcrumb: {
              fontSize: 16,
            },
          },
        }}>
        {children}
      </ConfigProvider>
    </StyleProvider>
  );
}
