"use client";

import React, { useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { Button, Card, Typography } from "antd";
import { ArrowLeft } from "lucide-react";
import PDFEvidenceViewer from "@/components/pdfviewer/PDFEvidenceViewer";
import { crossTokens } from "@/features/crossAnalysis/tokens";

const { Title, Text } = Typography;

// Use same-origin proxy via Next.js rewrites (/api -> BACKEND_URL)
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

function asInt(v: string | null, fallback: number) {
  if (!v) return fallback;
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.floor(n));
}

function safeDecode(v: string) {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

export default function CrossEvidencePage() {
  const sp = useSearchParams();
  const router = useRouter();

  // NOTE: upstream may encode query params; decode once here to avoid double-encoding.
  const fileId = safeDecode(sp.get("file_id") || "");
  const page = asInt(sp.get("page"), 1);
  const name = safeDecode(sp.get("name") || "Evidence");

  const fileUrl = useMemo(() => `${API_BASE_URL}/api/files/${encodeURIComponent(fileId)}/pdf`, [fileId]);

  if (!fileId) {
    return (
      <div style={{ flex: 1, background: crossTokens.color.bg, padding: crossTokens.spacing.xl }}>
        <Card style={{ borderRadius: crossTokens.radius.card, border: `1px solid ${crossTokens.color.border}` }}>
          <Title level={4} style={{ marginTop: 0 }}>Missing file_id</Title>
          <Text style={{ color: crossTokens.color.subtext }}>请从 Cross Analysis 的 Evidence 按钮进入。</Text>
          <div style={{ height: 12 }} />
          <Button onClick={() => router.back()}>返回</Button>
        </Card>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, background: crossTokens.color.bg, padding: 12 }}>
      <div style={{ maxWidth: "100%", margin: "0 auto", width: "100%" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
          <div>
            <Title level={3} style={{ margin: 0, color: crossTokens.color.text }}>{name}</Title>
            <Text style={{ color: crossTokens.color.subtext }}>大屏直读（默认适配宽度） · 可缩放 · 可跳页</Text>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button icon={<ArrowLeft size={16} />} onClick={() => router.back()} style={{ borderRadius: 12 }}>
              返回
            </Button>          </div>
        </div>

        <div style={{ height: 12 }} />

        <Card
          style={{
            borderRadius: crossTokens.radius.card,
            border: `1px solid ${crossTokens.color.border}`,
            boxShadow: crossTokens.shadow.card,
            background: crossTokens.color.card,
          }}
          bodyStyle={{ padding: 10, height: "calc(100vh - 92px)" }}
        >
          <PDFEvidenceViewer fileUrl={fileUrl} initialPage={page} height="calc(100vh - 124px)" defaultZoom={1.0} fitTo="page" />
        </Card>
      </div>
    </div>
  );
}
