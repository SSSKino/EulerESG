"use client";

import { useT } from "@/i18n/useT";

export default function SpecificationsSection() {
  const { t } = useT();

  return (
    <section className="px-6 py-20 md:py-28">
      <div className="mx-auto max-w-6xl">
        <div className="mb-12 text-center">
          <h1 className="text-3xl font-semibold md:text-4xl">{t("landing.whyTitle")}</h1>
          <p className="mx-auto mt-4 max-w-2xl text-[var(--brand-muted)]">{t("landing.whySubtitle")}</p>
        </div>
        <div className="brand-card overflow-hidden rounded-3xl">
          <div className="grid md:grid-cols-3">
            <div className="border-b border-black/6 p-8 md:border-b-0 md:border-r">
              <p className="text-sm font-medium text-[var(--brand-subtle)]">{t("landing.compareEuler")}</p>
              <ul className="mt-6 space-y-3 text-sm">
                <li className="flex gap-2 text-[var(--brand-primary)]">✓ {t("landing.compareEuler1")}</li>
                <li className="flex gap-2 text-[var(--brand-primary)]">✓ {t("landing.compareEuler2")}</li>
                <li className="flex gap-2 text-[var(--brand-primary)]">✓ {t("landing.compareEuler3")}</li>
                <li className="flex gap-2 text-[var(--brand-primary)]">✓ {t("landing.compareEuler4")}</li>
              </ul>
            </div>
            <div className="border-b border-black/6 p-8 text-[#6B6B6B] md:border-b-0 md:border-r">
              <p className="text-sm font-medium">{t("landing.compareManual")}</p>
              <ul className="mt-6 space-y-3 text-sm">
                <li>{t("landing.compareManual1")}</li>
                <li>{t("landing.compareManual2")}</li>
                <li>{t("landing.compareManual3")}</li>
              </ul>
            </div>
            <div className="p-8 text-[#6B6B6B]">
              <p className="text-sm font-medium">{t("landing.compareGeneric")}</p>
              <ul className="mt-6 space-y-3 text-sm">
                <li>{t("landing.compareGeneric1")}</li>
                <li>{t("landing.compareGeneric2")}</li>
                <li>{t("landing.compareGeneric3")}</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
