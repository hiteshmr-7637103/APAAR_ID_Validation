import { API_BASE, apiFetch, ApiError, tokenStore } from "./client";
import type { Role } from "../lib/roles";

export interface InstitutionSummary {
  id: number;
  code: string;
  name: string;
}

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  role: Role;
  institution: InstitutionSummary | null;
  student_apaar_id: string | null;
  active: boolean;
}

interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

/** OAuth2 password flow: form-encoded username (= email) and password. */
export async function login(email: string, password: string): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  });
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(
      res.status,
      json?.error ?? {
        code: "HTTP_" + res.status,
        message_en: "Sign-in failed.",
        message_key: "errors.http",
        details: {},
      },
    );
  }
  const body = json as TokenPair;
  tokenStore.set(body.access_token, body.refresh_token);
  return body.user;
}

export function fetchMe(): Promise<User> {
  return apiFetch<User>("/auth/me");
}

export function logout() {
  tokenStore.clear();
}
