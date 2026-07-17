"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function LogoutButton({ csrfToken }: { csrfToken: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    try {
      await fetch("/backend/api/session/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
        body: "{}",
      });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <button className="button button-tertiary" type="button" onClick={logout} disabled={pending}>
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}
