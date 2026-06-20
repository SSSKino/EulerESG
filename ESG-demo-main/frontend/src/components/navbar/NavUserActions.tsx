"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";
import { MdHome, MdSpaceDashboard } from "react-icons/md";
import { useAuthSession } from "@/hooks/useAuthSession";
import { useT } from "@/i18n/useT";
import LangToggle from "@/components/navbar/LangToggle";
import { UserAccountMenu } from "@/components/navbar/UserAccountMenu";

const pillBase = "app-nav-pill app-nav-pill--ghost inline-flex items-center justify-center gap-1.5";

function isAppShellRoute(pathname: string): boolean {
  return pathname.startsWith("/dashboard") || pathname.startsWith("/cross-analysis");
}

export function NavContextLink() {
  const pathname = usePathname() || "";
  const { t } = useT();
  const onAppShell = isAppShellRoute(pathname);

  if (onAppShell) {
    return (
      <Link href="/" className={pillBase}>
        <MdHome className="h-5 w-5 shrink-0" aria-hidden />
        <span>{t("nav.home")}</span>
      </Link>
    );
  }

  return (
    <Link href="/dashboard" className={pillBase}>
      <MdSpaceDashboard className="h-5 w-5 shrink-0" aria-hidden />
      <span>{t("nav.dashboard")}</span>
    </Link>
  );
}

type NavUserActionsProps = {
  showWelcome?: boolean;
};

export function NavUserActions({ showWelcome = true }: NavUserActionsProps) {
  const { auth } = useAuthSession();
  const { t } = useT();

  const displayName = auth?.name || auth?.email || "User";
  const welcomeName = useMemo(() => displayName, [displayName]);

  return (
    <>
      {showWelcome ? (
        <div className="hidden items-center gap-2 text-[17px] text-[var(--brand-muted)] sm:flex">
          <span>{t("nav.welcome")}</span>
          <span className="max-w-[160px] truncate font-medium text-[var(--brand-text)] md:max-w-[200px]">
            {welcomeName}
          </span>
        </div>
      ) : null}

      <NavContextLink />
      <LangToggle />
      <UserAccountMenu
        triggerClassName="h-10 w-10 rounded-full p-0 sm:h-11 sm:w-11"
        avatarClassName="h-9 w-9 sm:h-10 sm:w-10"
        fallbackClassName="text-base font-medium"
      />
    </>
  );
}
