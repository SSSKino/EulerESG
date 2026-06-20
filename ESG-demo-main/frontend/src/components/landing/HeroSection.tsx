"use client";

import Image from "next/image";
import { useT } from "@/i18n/useT";

function HeroDashboardVisual() {
  return (
    <div className="relative mx-auto w-full max-w-7xl">
      <div className="rounded-[2.75rem] bg-[#6D7560] px-8 py-12 md:rounded-[3.5rem] md:px-14 md:py-16 lg:px-16 lg:py-20">
        <div className="relative mx-auto w-full max-w-5xl overflow-hidden rounded-[2rem] border border-white/20 shadow-2xl shadow-black/25 md:rounded-[2.25rem]">
          <div className="relative aspect-[4/3] w-full md:aspect-[16/10] lg:min-h-[420px]">
            <Image
              src="/hero-esg.jpg"
              alt="Hands holding a young plant — sustainability and environmental stewardship"
              fill
              className="object-cover"
              sizes="(max-width: 768px) 100vw, (max-width: 1200px) 90vw, 1024px"
              priority
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function HeroSection() {
  const { t } = useT();

  return (
    <section className="flex min-h-[calc(100vh-5rem)] flex-col px-6 pb-20 pt-8 md:px-10 md:pt-12">
      <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col">
        <div className="text-center">
          <div className="mb-6 flex justify-center md:mb-8">
            <Image
              src="/Euler-Img.svg"
              alt="Euler ESG"
              width={72}
              height={62}
              className="md:hidden"
              priority
            />
            <Image
              src="/Euler-Img.svg"
              alt="Euler ESG"
              width={96}
              height={83}
              className="hidden md:block lg:w-[112px] lg:h-auto"
              priority
            />
          </div>
          <h1 className="font-serif text-6xl leading-[1.02] tracking-tight sm:text-7xl md:text-8xl lg:text-[6.5rem] xl:text-[7.5rem]">
            Euler ESG
          </h1>
          <p className="mx-auto mt-6 max-w-3xl text-base text-[#5C5C5C] md:mt-8 md:text-xl lg:text-2xl">
            {t("landing.badge")}
          </p>
        </div>
        <div className="mt-12 flex flex-1 flex-col justify-end md:mt-16 lg:mt-20">
          <HeroDashboardVisual />
        </div>
      </div>
    </section>
  );
}
