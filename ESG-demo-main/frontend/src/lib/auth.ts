"use client";

export const AUTH_TOKEN_KEY = "auth_token";
export const AUTH_USER_KEY = "auth_user";

export interface StoredAuth {
  token: string;
  userId: number | string;
  email?: string;
  name?: string;
}

const isBrowser = typeof window !== "undefined";
export const AUTH_CHANGE_EVENT = "euler-auth-change";

function notifyAuthChange() {
  if (!isBrowser) return;
  window.dispatchEvent(new Event(AUTH_CHANGE_EVENT));
}

export function subscribeAuthChange(listener: () => void) {
  if (!isBrowser) return () => {};
  window.addEventListener(AUTH_CHANGE_EVENT, listener);
  return () => window.removeEventListener(AUTH_CHANGE_EVENT, listener);
}

export function saveAuth(auth: StoredAuth) {
  if (!isBrowser) return;
  localStorage.setItem(AUTH_TOKEN_KEY, auth.token);
  localStorage.setItem(AUTH_USER_KEY, JSON.stringify(auth));
  notifyAuthChange();
}

export function clearAuth() {
  if (!isBrowser) return;
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
  notifyAuthChange();
}

export function getStoredAuth(): StoredAuth | null {
  if (!isBrowser) return null;

  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const rawUser = localStorage.getItem(AUTH_USER_KEY);

  if (!token && !rawUser) return null;

  try {
    const parsed = rawUser ? (JSON.parse(rawUser) as StoredAuth) : undefined;
    return {
      token: token || parsed?.token || "",
      userId: parsed?.userId ?? "",
      email: parsed?.email,
      name: parsed?.name,
    };
  } catch {
    return token ? { token, userId: "" } : null;
  }
}

export function isAuthenticated(): boolean {
  return Boolean(getStoredAuth()?.token);
}

