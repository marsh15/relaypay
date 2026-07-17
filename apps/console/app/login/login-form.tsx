"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import type { ApiError } from "@/lib/types";

export function LoginForm({ destination }: { destination: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/backend/api/session/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
      });
      if (!response.ok) {
        const payload = (await response.json()) as ApiError;
        const wait = response.headers.get("retry-after");
        setError(
          response.status === 429
            ? `Too many attempts. Try again in ${wait ?? "a few"} seconds.`
            : (payload.error?.message ?? "Sign-in is temporarily unavailable."),
        );
        return;
      }
      router.replace(destination);
      router.refresh();
    } catch {
      setError("The console cannot reach RelayPay. Check service readiness and try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="login-form" onSubmit={submit} aria-describedby={error ? "login-error" : undefined}>
      {error ? (
        <div id="login-error" className="form-error" role="alert">
          {error}
        </div>
      ) : null}
      <label htmlFor="email">Administrator email</label>
      <input id="email" name="email" type="email" autoComplete="username" required />
      <label htmlFor="password">Password</label>
      <input
        id="password"
        name="password"
        type="password"
        autoComplete="current-password"
        minLength={8}
        required
      />
      <button className="button button-primary" type="submit" disabled={pending}>
        {pending ? "Verifying…" : "Sign in"}
      </button>
      <p className="sr-only" aria-live="polite">
        {pending ? "Verifying administrator credentials" : ""}
      </p>
    </form>
  );
}
