"use client";

import React, { useMemo } from "react";
import { App as AntdApp, ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { useAppLang } from "@/i18n/useAppLang";

export function AntdRegistry({ children }: { children: React.ReactNode }) {
  const { lang } = useAppLang();

  const locale = useMemo(() => (lang === "zh" ? zhCN : enUS), [lang]);

  return (
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
      <AntdApp component={false}>{children}</AntdApp>
    </ConfigProvider>
  );
}
