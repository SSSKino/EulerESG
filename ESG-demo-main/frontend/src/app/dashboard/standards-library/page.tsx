"use client";

import dynamic from "next/dynamic";

function StandardsLibraryLoading() {
  return (
    <section
      data-testid="standards-library-loading"
      aria-busy="true"
      aria-label="Loading Standards Library"
      className="animate-pulse space-y-5"
    >
      <div className="h-12 w-80 rounded-xl bg-slate-100" />
      <div className="h-16 rounded-2xl border border-slate-200 bg-white" />
      <div className="h-[520px] rounded-2xl border border-slate-200 bg-white" />
    </section>
  );
}

const FrameworkReferencePanel = dynamic(
  () => import("@/components/maincontent/FrameworkReferencePanel"),
  { ssr: false, loading: StandardsLibraryLoading },
);

export default function StandardsLibraryPage() {
  return (
    <main className="min-h-screen w-full bg-white">
      <div className="mx-auto w-full max-w-[1540px] px-5 py-8 sm:px-8 lg:px-12 lg:py-10">
        <FrameworkReferencePanel />
      </div>
    </main>
  );
}
