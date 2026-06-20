"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiService } from "@/lib/api";
import { isAuthenticated, saveAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";
import AuthCard from "@/components/auth/AuthCard";
import AuthPageLayout from "@/components/auth/AuthPageLayout";
import AuthSocialSection from "@/components/auth/AuthSocialSection";
import AuthSubmitSection from "@/components/auth/AuthSubmitSection";

export default function RegisterPage() {
  const router = useRouter();
  const { t } = useT();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const termsRequiredMessage = t("auth.termsRequired");

  const handleSocialClick = (provider: "Google" | "Apple") => {
    setError(t("auth.socialSignUpComingSoon", { provider }));
  };

  useEffect(() => {
    if (isAuthenticated()) {
      router.replace("/dashboard");
    }
  }, [router]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);

    if (!agreedToTerms) {
      setError(termsRequiredMessage);
      return;
    }

    if (password !== confirmPassword) {
      setError(t("auth.passwordsNotMatch"));
      return;
    }

    setLoading(true);
    try {
      const result = await apiService.register(name, email, password);
      saveAuth({
        token: result.token,
        userId: result.userId,
        email,
        name: result.name || name,
      });
      router.push("/dashboard");
    } catch (err) {
      const message = err instanceof Error ? err.message : t("auth.registrationFailed");
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthPageLayout>
      <AuthCard>
        <div className="mb-8 shrink-0">
          <h1 className="text-4xl font-semibold tracking-tight text-[var(--brand-text)] md:text-5xl">
            {t("auth.createAccount")}
          </h1>
        </div>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
          <div className="flex min-h-0 flex-1 flex-col space-y-3 overflow-y-auto pr-0.5">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-[var(--brand-text)]">{t("auth.name")}</label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("auth.name")}
                required
                className="h-11 rounded-xl border-black/10 bg-white/95"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium text-[var(--brand-text)]">{t("auth.email")}</label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={t("auth.emailPlaceholder")}
                required
                className="h-11 rounded-xl border-black/10 bg-white/95"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium text-[var(--brand-text)]">{t("auth.password")}</label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("auth.passwordPlaceholder")}
                required
                className="h-11 rounded-xl border-black/10 bg-white/95"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium text-[var(--brand-text)]">{t("auth.confirmPassword")}</label>
              <Input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder={t("auth.confirmPassword")}
                required
                className="h-11 rounded-xl border-black/10 bg-white/95"
              />
            </div>

            <div className="flex items-start gap-2.5 pt-0.5">
              <input
                id="terms-agree"
                type="checkbox"
                checked={agreedToTerms}
                onChange={(e) => {
                  setAgreedToTerms(e.target.checked);
                  if (e.target.checked && error === termsRequiredMessage) {
                    setError(null);
                  }
                }}
                className="mt-0.5 size-4 shrink-0 rounded border-black/20 accent-[var(--brand-primary)]"
              />
              <p className="text-sm leading-5 text-[var(--brand-text)]">
                <label htmlFor="terms-agree" className="cursor-pointer">
                  {t("auth.agreeToTermsBefore")}
                </label>
                <Link
                  href="/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="brand-link underline"
                >
                  {t("auth.termsAndPolicy")}
                </Link>
              </p>
            </div>
          </div>

          <AuthSubmitSection error={error}>
            <Button type="submit" className="brand-primary-button h-11 w-full rounded-xl text-base" disabled={loading}>
              {loading ? t("auth.registering") : t("auth.register")}
            </Button>
          </AuthSubmitSection>
        </form>

        <AuthSocialSection mode="register" onSocialClick={handleSocialClick} />

        <p className="mt-4 shrink-0 text-center text-sm text-[var(--brand-subtle)]">
          {t("auth.haveAccount")}{" "}
          <Link href="/login" className="brand-link font-medium hover:underline">
            {t("auth.signInLink")}
          </Link>
        </p>
      </AuthCard>
    </AuthPageLayout>
  );
}
