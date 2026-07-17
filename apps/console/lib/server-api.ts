import "server-only";

import { cookies } from "next/headers";
import type { Session } from "@/lib/types";

const backendUrl = process.env.INTERNAL_API_BASE_URL ?? "http://localhost:8000";

export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const cookieStore = await cookies();
  return fetch(`${backendUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      Cookie: cookieStore.toString(),
      ...init?.headers,
    },
  });
}

export async function getSession(): Promise<Session | null> {
  const response = await backendFetch("/api/session/me");
  if (!response.ok) return null;
  return (await response.json()) as Session;
}
