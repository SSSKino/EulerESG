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
            colorBgContainer: "#fff",
            borderRadiusLG: 8,
            fontFamily:
              "var(--font-inter), -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji'",
          },
        }}>
        {children}
      </ConfigProvider>
    </StyleProvider>
  );
}
