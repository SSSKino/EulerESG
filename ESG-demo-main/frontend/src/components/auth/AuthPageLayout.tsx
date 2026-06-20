import Image from "next/image";
import MarketingShell from "@/components/landing/MarketingShell";
import { AUTH_IMAGE_HEIGHT } from "./authLayout";

type AuthPageLayoutProps = {
  children: React.ReactNode;
};

export default function AuthPageLayout({ children }: AuthPageLayoutProps) {
  return (
    <MarketingShell>
      <div className="mx-auto flex min-h-[calc(100vh-5rem-88px)] w-full max-w-7xl items-center justify-center px-4 py-10 md:px-6">
        <div className="relative w-full overflow-hidden rounded-[2rem] shadow-xl">
          <div className={`relative ${AUTH_IMAGE_HEIGHT} w-full`}>
            <Image
              src="/login-hero.jpg"
              alt="Savings jar with a growing plant"
              fill
              className="object-cover"
              sizes="(max-width: 1280px) 100vw, 1200px"
              priority
            />
            <div className="absolute inset-0 bg-gradient-to-r from-white/88 via-white/42 to-white/0" />
            <div
              className={`relative z-10 flex ${AUTH_IMAGE_HEIGHT} items-center px-6 py-14 md:px-12 lg:px-16`}
            >
              {children}
            </div>
          </div>
        </div>
      </div>
    </MarketingShell>
  );
}
