"use client";

import { ExternalLink } from "lucide-react";

import { useT } from "@/i18n/useT";

const FRAMEWORK_REFERENCES = [
  {
    name: "SASB",
    asOf: "Jan 2026",
    href: "https://www.ifrs.org/issued-standards/sasb-standards/",
  },
  {
    name: "GRI",
    asOf: "Jun 2026",
    href: "https://www.globalreporting.org/standards/gri-standards-download-center/",
  },
  {
    name: "CDP",
    asOf: "Apr 2026",
    href: "https://www.cdp.net/en/disclosure-2026",
  },
  {
    name: "AASB",
    asOf: "Nov 2025",
    href: "https://standards.aasb.gov.au/sustainability-reporting-standards",
  },
] as const;

export default function FrameworkReferencePanel() {
  const { lang } = useT();
  const title = lang === "zh" ? "标准库" : "Standards Library";

  return (
    <section
      aria-labelledby="standards-library-title"
      className="w-full rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:p-6"
      data-testid="standards-library"
    >
      <header className="mb-5 border-b border-slate-200/70 pb-4">
        <h1
          id="standards-library-title"
          className="m-0 text-xl font-semibold tracking-[-0.02em] text-slate-900"
        >
          {title}
        </h1>
        <p className="mb-0 mt-1.5 text-sm text-slate-500">
          {lang === "zh"
            ? "查看当前采用的可持续发展披露标准版本。"
            : "Browse the sustainability disclosure standards currently referenced by the platform."}
        </p>
      </header>

      <ul
        aria-label={lang === "zh" ? "标准库链接" : "Standards library links"}
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3"
      >
        {FRAMEWORK_REFERENCES.map((reference) => {
          const label = `View ${reference.name} as of ${reference.asOf}`;
          return (
            <li key={reference.name} className="min-w-0 list-none">
              <a
                href={reference.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={label}
                className="group flex min-h-[112px] flex-col justify-between rounded-xl border border-slate-200 bg-slate-50/40 p-4 text-slate-700 transition-[border-color,background-color,box-shadow] duration-150 hover:border-blue-200 hover:bg-blue-50/40 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] focus-visible:ring-offset-2"
              >
                <span className="flex items-start justify-between gap-3">
                  <span className="text-base font-semibold tracking-[-0.01em] text-slate-900 group-hover:text-[#2274BC]">
                    {reference.name}
                  </span>
                  <ExternalLink aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400 group-hover:text-[#2274BC]" />
                </span>
                <span className="text-sm text-slate-500">as of {reference.asOf}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
