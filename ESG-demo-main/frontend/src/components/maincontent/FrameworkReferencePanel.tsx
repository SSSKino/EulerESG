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
    name: "TCFD",
    asOf: "Oct 2023",
    href: "https://www.fsb-tcfd.org/recommendations/",
  },
  {
    name: "CDP",
    asOf: "Apr 2026",
    href: "https://www.cdp.net/en/disclosure-2026",
  },
  {
    name: "AASB",
    asOf: "Nov 2025",
    href: null,
  },
] as const;

export default function FrameworkReferencePanel() {
  const { lang } = useT();

  return (
    <aside
      aria-labelledby="framework-views-title"
      className="self-stretch border-t border-slate-200/70 bg-slate-50/40 p-4 sm:p-5 lg:border-l lg:border-t-0"
      data-testid="framework-reference-panel"
    >
      <div className="mb-2.5">
        <h2 id="framework-views-title" className="text-[15px] font-semibold tracking-[-0.01em] text-slate-900">
          {lang === "zh" ? "框架参考" : "Framework references"}
        </h2>
      </div>

      <nav
        aria-label={lang === "zh" ? "披露框架参考链接" : "Disclosure framework reference links"}
        className="divide-y divide-slate-200/70"
      >
        {FRAMEWORK_REFERENCES.map((reference) => {
          const label = `View ${reference.name} as of ${reference.asOf}`;
          const content = (
            <>
              <span className="w-20 shrink-0 text-xs font-semibold tracking-[0.02em] text-slate-700 transition-colors group-hover:text-[#2274BC]">
                View {reference.name}
              </span>
              <span className="min-w-0 flex-1 text-[13px] text-slate-500 transition-colors group-hover:text-slate-700">as of {reference.asOf}</span>
            </>
          );

          return reference.href ? (
            <a
              key={reference.name}
              href={reference.href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={label}
              className="group flex min-h-9 items-center gap-2 rounded-lg px-2.5 py-1.5 text-slate-700 transition-colors duration-150 hover:bg-white hover:text-[#2274BC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] focus-visible:ring-offset-2"
            >
              {content}
              <ExternalLink aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400 group-hover:text-[#2274BC]" />
            </a>
          ) : (
            <div
              key={reference.name}
              role="group"
              aria-label={label}
              aria-disabled="true"
              title={lang === "zh" ? "参考内容将在后续补充" : "Reference content will be added later"}
              className="flex min-h-9 cursor-not-allowed items-center gap-2 rounded-lg px-2.5 py-1.5 opacity-50"
              data-framework-placeholder={reference.name}
            >
              {content}
              <span aria-hidden="true" className="h-4 w-4 shrink-0" />
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
