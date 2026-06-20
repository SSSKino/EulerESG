"use client";

import { FaApple, FaGoogle } from "react-icons/fa";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/useT";

type AuthMode = "login" | "register";

type AuthSocialSectionProps = {
  mode: AuthMode;
  onSocialClick: (provider: "Google" | "Apple") => void;
};

export default function AuthSocialSection({ mode, onSocialClick }: AuthSocialSectionProps) {
  const { t } = useT();
  const googleLabel = mode === "login" ? t("auth.signInWithGoogle") : t("auth.signUpWithGoogle");
  const appleLabel = mode === "login" ? t("auth.signInWithApple") : t("auth.signUpWithApple");

  return (
    <div className="shrink-0 space-y-4">
      <div className="flex items-center gap-4">
        <div className="h-px flex-1 bg-black/8" />
        <span className="text-xs uppercase tracking-[0.16em] text-[var(--brand-subtle)]">
          {t("auth.orContinueWith")}
        </span>
        <div className="h-px flex-1 bg-black/8" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Button
          type="button"
          variant="outline"
          className="h-12 rounded-xl border border-[#DADCE0] bg-white px-4 text-[#3C4043] shadow-none hover:bg-[#F8F9FA] hover:text-[#3C4043]"
          onClick={() => onSocialClick("Google")}
        >
          <FaGoogle className="size-[18px] text-[#4285F4]" />
          {googleLabel}
        </Button>
        <Button
          type="button"
          className="h-12 rounded-xl border border-black bg-black px-4 text-white shadow-none hover:bg-[#1f1f1f] hover:text-white"
          onClick={() => onSocialClick("Apple")}
        >
          <FaApple className="size-[18px] text-white" />
          {appleLabel}
        </Button>
      </div>
    </div>
  );
}
