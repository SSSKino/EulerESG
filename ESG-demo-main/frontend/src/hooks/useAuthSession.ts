"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { getStoredAuth, subscribeAuthChange, type StoredAuth } from "@/lib/auth";

export function useAuthSession() {
  const pathname = usePathname();
  const [auth, setAuth] = useState<StoredAuth | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(() => {
    setAuth(getStoredAuth());
    setReady(true);
  }, []);

  useEffect(() => {
    refresh();
    return subscribeAuthChange(refresh);
  }, [refresh, pathname]);

  return {
    auth,
    isLoggedIn: Boolean(auth?.token),
    ready,
    refresh,
  };
}
