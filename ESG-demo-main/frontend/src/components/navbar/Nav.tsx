"use client";

import EulerLogo from "@/assets/Euler-Img.svg";
import Image from "next/image";
import Link from "next/link";
import { useT } from "@/i18n/useT";
import { NavUserActions } from "@/components/navbar/NavUserActions";

export default function Nav({ className }: { className?: string }) {
  const { t } = useT();

  return (
    <nav
      className={`flex w-full items-center justify-between border-b border-black/6 bg-[color:var(--brand-surface)]/92 px-4 py-3 backdrop-blur-md md:px-6 ${className ?? ""}`}
    >
      <Link href="/" className="flex items-center gap-4" aria-label={t("nav.home")}>
        <Image src={EulerLogo} alt="Euler Logo" className="h-auto w-8 sm:w-10" />
        <h1 className="!text-base font-semibold text-[var(--brand-accent)] sm:!text-xl">Euler ESG</h1>
      </Link>

      <div className="flex items-center gap-2 sm:gap-3">
        <NavUserActions />
      </div>
    </nav>
  );
}
