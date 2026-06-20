"use client";

import Link from "next/link";
import MarketingLayout from "@/components/layouts/MarketingLayout";
import { useT } from "@/i18n/useT";

export default function TermsPage() {
  const { t } = useT();

  return (
    <MarketingLayout>
      <div className="mx-auto max-w-3xl px-4 py-16 md:px-24 md:py-24">
        <h1 className="text-4xl font-semibold tracking-tight text-[var(--brand-text)] md:text-5xl">
          {t("legal.termsTitle")}
        </h1>
        <p className="mt-6 text-base leading-7 text-[var(--brand-subtle)]">{t("legal.termsPlaceholder")}</p>
        <Link href="/register" className="brand-link mt-8 inline-block text-sm font-medium hover:underline">
          {t("legal.backToRegister")}
        </Link>
      </div>
    </MarketingLayout>
  );
}
