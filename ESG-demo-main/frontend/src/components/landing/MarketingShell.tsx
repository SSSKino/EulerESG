"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo } from "react";
import LangToggle from "@/components/navbar/LangToggle";
import { Button } from "@/components/ui/button";
import { useAuthSession } from "@/hooks/useAuthSession";
import { NavUserActions } from "@/components/navbar/NavUserActions";
import { useAppLang } from "@/i18n/useAppLang";
import { useT } from "@/i18n/useT";
import FrameworkBadges from "./FrameworkBadges";

const NAV_ROUTES = [
  { href: "/benefits", labelKey: "landing.navBenefits" },
  { href: "/specifications", labelKey: "landing.navFrameworks" },
  { href: "/how-to", labelKey: "landing.navHowTo" },
  { href: "/contact", labelKey: "landing.navContact" },
] as const;

function BrandLogo({ size = 32 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <Image src="/Euler-Img.svg" alt="Euler ESG" width={size} height={Math.round(size * 0.86)} priority />
      <span className="text-lg font-semibold tracking-tight text-[var(--brand-accent)] md:text-xl">
        Euler ESG
      </span>
    </span>
  );
}

export default function MarketingShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { isLoggedIn } = useAuthSession();
  const { lang } = useAppLang();
  const { t } = useT();

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang]);

  const nav = useMemo(
    () => NAV_ROUTES.map((item) => ({ ...item, label: t(item.labelKey) })),
    [t]
  );

  return (
    <div className="brand-page min-h-screen">
      <header className="fixed inset-x-0 top-0 z-50 w-full border-b border-black/5 bg-[color:var(--brand-surface)]/90 backdrop-blur-md">
        <div className="flex min-h-20 w-full items-center gap-6 px-6 md:px-10 lg:px-14 xl:px-16">
          <Link href="/" className="shrink-0 text-base md:text-lg">
            <BrandLogo size={36} />
          </Link>
          <nav className="hidden flex-1 items-center justify-center gap-10 text-base font-semibold tracking-[0.03em] text-[#3D3D3D] md:flex lg:gap-14 xl:gap-16 xl:text-[17px]">
            {nav.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`whitespace-nowrap transition-colors hover:text-[var(--brand-primary)] ${
                    active ? "font-bold text-[var(--brand-primary)]" : ""
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
            <FrameworkBadges className="hidden border-r border-black/10 pr-3 md:flex lg:pr-5" />
            {isLoggedIn ? (
              <NavUserActions />
            ) : (
              <>
                <LangToggle />
                <Button
                  asChild
                  className="app-nav-pill app-nav-pill--primary inline-flex h-8 px-4 text-xs sm:text-sm"
                >
                  <Link href="/login">{t("landing.navLogin")}</Link>
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      <main className="pt-20">{children}</main>

      <footer className="border-t border-black/5 px-6 py-10 text-center text-sm text-[#6B6B6B]">
        <p>
          © {new Date().getFullYear()} Euler ESG. {t("landing.footer")}
        </p>
      </footer>
    </div>
  );
}
