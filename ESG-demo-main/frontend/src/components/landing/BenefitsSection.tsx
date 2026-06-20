"use client";

import { useMemo } from "react";
import { FileSearch, Layers, MessageSquare, ShieldCheck } from "lucide-react";
import { useT } from "@/i18n/useT";

export default function BenefitsSection() {
  const { t } = useT();

  const benefits = useMemo(
    () => [
      { icon: FileSearch, title: t("landing.benefitPdfTitle"), description: t("landing.benefitPdfDesc") },
      { icon: Layers, title: t("landing.benefitDualTitle"), description: t("landing.benefitDualDesc") },
      {
        icon: ShieldCheck,
        title: t("landing.benefitFrameworkTitle"),
        description: t("landing.benefitFrameworkDesc"),
      },
      { icon: MessageSquare, title: t("landing.benefitChatTitle"), description: t("landing.benefitChatDesc") },
    ],
    [t]
  );

  const highlights = useMemo(
    () => [t("landing.highlight1"), t("landing.highlight2"), t("landing.highlight3"), t("landing.highlight4")],
    [t]
  );

  return (
    <>
      <section className="px-6 py-20 md:py-28">
        <div className="mx-auto max-w-6xl">
          <div className="mb-14 max-w-2xl">
            <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">{t("landing.benefitsTitle")}</h1>
            <p className="mt-4 text-lg text-[var(--brand-muted)]">{t("landing.benefitsSubtitle")}</p>
          </div>
          <div className="grid gap-6 sm:grid-cols-2">
            {benefits.map(({ icon: Icon, title, description }) => (
              <article
                key={title}
                className="brand-card rounded-3xl p-8 transition-shadow hover:shadow-md"
              >
                <div className="mb-5 flex size-12 items-center justify-center rounded-2xl bg-[var(--brand-accent-soft)]">
                  <Icon className="size-6 text-[var(--brand-accent)]" />
                </div>
                <h2 className="text-xl font-semibold">{title}</h2>
                <p className="mt-3 leading-relaxed text-[var(--brand-muted)]">{description}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="px-6 py-20 md:py-28">
        <div className="mx-auto grid max-w-6xl gap-12 lg:grid-cols-2 lg:items-center">
          <div className="overflow-hidden rounded-[2rem] bg-gradient-to-br from-[var(--brand-gradient-from)] to-[var(--brand-gradient-to)] p-10 text-white md:p-14">
            <h2 className="text-3xl font-semibold leading-tight md:text-4xl">{t("landing.bigPictureTitle")}</h2>
            <p className="mt-4 leading-relaxed text-white/85">{t("landing.bigPictureDesc")}</p>
          </div>
          <ol className="space-y-6">
            {highlights.map((text, index) => (
              <li key={text} className="flex gap-4">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-[var(--brand-accent-soft)] text-sm font-semibold text-[var(--brand-accent)]">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <p className="pt-2 leading-relaxed text-[var(--brand-muted)]">{text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </>
  );
}
