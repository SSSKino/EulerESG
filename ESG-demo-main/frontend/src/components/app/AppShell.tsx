"use client";

import Nav from "@/components/navbar/Nav";

type AppShellProps = {
  children: React.ReactNode;
};

export default function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <Nav className="app-nav" />
      <main className="app-main">{children}</main>
    </div>
  );
}
