"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiService } from "@/lib/api";
import { getStoredAuth, isAuthenticated, saveAuth } from "@/lib/auth";
import { useT } from "@/i18n/useT";
import AuthCard from "@/components/auth/AuthCard";
import AuthPageLayout from "@/components/auth/AuthPageLayout";
import AuthSocialSection from "@/components/auth/AuthSocialSection";
import AuthSubmitSection from "@/components/auth/AuthSubmitSection";

export default function LoginPage() {
  const router = useRouter();
  const { t } = useT();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSocialClick = (provider: "Google" | "Apple") => {
    setError(t("auth.socialComingSoon", { provider }));
  };

  useEffect(() => {
    if (isAuthenticated()) {
      router.replace("/dashboard");
    }
  }, [router]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await apiService.login(email, password);
      const existing = getStoredAuth();
      const name = result.name || existing?.name;
      saveAuth({ token: result.token, userId: result.userId, email, name });
      router.push("/dashboard");
    } catch (err) {
      const message = err instanceof Error ? err.message : t("auth.loginFailed");
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
            {t("auth.signInWelcome")}
          </h1>
          <p className="mt-3 text-sm leading-6 text-[var(--brand-subtle)] md:text-base">
            {t("auth.signInHelper")}
          </p>
        </div>

        <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
          <div className="flex min-h-0 flex-1 flex-col space-y-3">
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

            <div className="flex-1" />
          </div>

          <AuthSubmitSection error={error}>
            <Button type="submit" className="brand-primary-button h-11 w-full rounded-xl text-base" disabled={loading}>
              {loading ? t("auth.signingIn") : t("auth.signIn")}
            </Button>
          </AuthSubmitSection>
        </form>

        <AuthSocialSection mode="login" onSocialClick={handleSocialClick} />

        <p className="mt-4 shrink-0 text-center text-sm text-[var(--brand-subtle)]">
          {t("auth.noAccount")}{" "}
          <Link href="/register" className="brand-link font-medium hover:underline">
            {t("auth.createOne")}
          </Link>
        </p>
      </AuthCard>
    </AuthPageLayout>
  );
}
