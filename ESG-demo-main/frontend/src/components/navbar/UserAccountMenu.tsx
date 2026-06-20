"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { MdLogout, MdSettings, MdPerson } from "react-icons/md";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { clearAuth } from "@/lib/auth";
import { useAuthSession } from "@/hooks/useAuthSession";
import { useFileStore } from "@/store/useFileStore";
import { useT } from "@/i18n/useT";

type UserAccountMenuProps = {
  triggerClassName?: string;
  avatarClassName?: string;
  fallbackClassName?: string;
};

export function UserAccountMenu({
  triggerClassName = "h-10 w-10 rounded-full p-0 sm:h-12 sm:w-12",
  avatarClassName = "h-8 w-8",
  fallbackClassName = "text-sm sm:text-base",
}: UserAccountMenuProps) {
  const router = useRouter();
  const { auth } = useAuthSession();
  const clearFiles = useFileStore((s) => s.clearFiles);
  const { t } = useT();

  const displayName = auth?.name || auth?.email || "User";
  const initials = useMemo(() => {
    const ch = displayName.trim().slice(0, 1);
    return (ch || "U").toUpperCase();
  }, [displayName]);

  const handleLogout = () => {
    clearAuth();
    clearFiles();
    router.push("/");
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className={triggerClassName} aria-label={t("nav.userMenu")}>
          <Avatar className={avatarClassName}>
            <AvatarFallback className={fallbackClassName}>{initials}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[150px] sm:min-w-[180px]">
        <DropdownMenuLabel className="text-sm sm:text-base">{t("nav.myAccount")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-sm sm:text-base">
          <MdPerson className="mr-2" />
          <span>{t("nav.profile")}</span>
        </DropdownMenuItem>
        <DropdownMenuItem className="text-sm sm:text-base">
          <MdSettings className="mr-2" />
          <span>{t("nav.settings")}</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-sm sm:text-base" onClick={handleLogout}>
          <MdLogout className="mr-2" />
          <span>{t("nav.logout")}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
