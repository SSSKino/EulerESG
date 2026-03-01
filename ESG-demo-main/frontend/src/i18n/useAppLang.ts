"use client";

import { useEffect, useSyncExternalStore } from "react";
import { getLang, initLang, setLang, subscribeLang } from "./langStore";
import type { Lang } from "./dict";

export function useAppLang(): { lang: Lang; setLang: (v: Lang) => void } {
  const lang = useSyncExternalStore(subscribeLang, getLang, () => "zh");

  useEffect(() => {
    initLang();
  }, []);

  return { lang, setLang };
}
