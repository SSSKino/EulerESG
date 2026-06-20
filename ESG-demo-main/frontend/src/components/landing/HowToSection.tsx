"use client";

import { useMemo } from "react";
import { useT } from "@/i18n/useT";

export default function HowToSection() {
  const { t } = useT();

  const steps = useMemo(
    () => [
      { step: "01", title: t("landing.step1Title"), description: t("landing.step1Desc") },
      { step: "02", title: t("landing.step2Title"), description: t("landing.step2Desc") },
      { step: "03", title: t("landing.step3Title"), description: t("landing.step3Desc") },
    ],
    [t]
  );

  return (
    <section className="px-6 py-20 md:py-28">
      <div className="mx-auto max-w-6xl">
        <div className="mb-14 text-center">
          <h1 className="text-3xl font-semibold md:text-4xl">{t("landing.howToTitle")}</h1>
          <p className="mt-4 text-[var(--brand-muted)]">{t("landing.howToSubtitle")}</p>
        </div>
        <div className="grid gap-8 md:grid-cols-3">
          {steps.map(({ step, title, description }) => (
            <article key={step} className="brand-card rounded-3xl p-8">
              <span className="text-4xl font-light text-[color:rgb(47_123_189_/_0.4)]">{step}</span>
              <h2 className="mt-4 text-xl font-semibold">{title}</h2>
              <p className="mt-3 leading-relaxed text-[var(--brand-muted)]">{description}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
