import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getSession } from "@/lib/server-api";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

function safeNext(value: string | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/lab";
  if (value === "/lab" || value.startsWith("/payments/")) return value;
  return "/lab";
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  if (await getSession()) redirect("/lab");
  const destination = safeNext((await searchParams).next);
  return (
    <main id="main-content" className="login-shell">
      <section className="login-intro" aria-labelledby="product-name">
        <p className="eyebrow">Payment orchestration, under inspection</p>
        <h1 id="product-name" className="wordmark">
          RelayPay
        </h1>
        <p className="claim">
          Trace exactly what committed when a provider response disappears between effect and
          acknowledgement.
        </p>
        <div className="synthetic-notice" role="note">
          <strong>Synthetic data only.</strong> This sandbox never accepts real payment credentials
          or personal financial information.
        </div>
      </section>
      <section className="login-panel" aria-labelledby="login-title">
        <div>
          <p className="section-kicker">Forensic console</p>
          <h2 id="login-title">Sign in to inspect a demo tenant</h2>
          <p className="supporting-copy">
            Use one of the seeded administrator identities from the project quickstart.
          </p>
        </div>
        <LoginForm destination={destination} />
        <p className="login-footnote">
          Sessions are opaque, server-side, and isolated by organisation.
        </p>
      </section>
    </main>
  );
}
