type AuthSubmitSectionProps = {
  error?: string | null;
  children: React.ReactNode;
};

/** Pins primary button to the same vertical slot on login and register. */
export default function AuthSubmitSection({ error, children }: AuthSubmitSectionProps) {
  return (
    <div className="shrink-0 space-y-3 pb-1 mb-5">
      {error ? (
        <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
      ) : null}
      {children}
    </div>
  );
}
