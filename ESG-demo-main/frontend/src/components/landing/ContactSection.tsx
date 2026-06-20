"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/useT";
import { useAuthSession } from "@/hooks/useAuthSession";

export default function ContactSection() {
  const { t } = useT();
  const { isLoggedIn } = useAuthSession();

  return (
    <section className="px-6 pb-24 pt-8 md:pt-12">
      <div className="mx-auto max-w-6xl overflow-hidden rounded-[2rem] bg-[var(--brand-text)] px-8 py-14 text-center text-white md:px-16">
        <h1 className="text-3xl font-semibold md:text-4xl">{t("landing.ctaTitle")}</h1>
        <p className="mx-auto mt-4 max-w-xl text-white/70">{t("landing.ctaSubtitle")}</p>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          <Button asChild size="lg" className="bg-white px-8 text-[var(--brand-text)] hover:bg-white/90">
            <Link href={isLoggedIn ? "/dashboard" : "/login"}>
              {isLoggedIn ? t("landing.openDashboard") : t("landing.ctaButton")}
            </Link>
          </Button>
        </div>
      </div>
    </section>
  );
}
