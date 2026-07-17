import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { getSession } from "@/lib/server-api";
import { ScenarioLab } from "./scenario-lab";

export const metadata: Metadata = { title: "Scenario Lab" };

export default async function LabPage() {
  const session = await getSession();
  if (!session) redirect("/login?next=/lab");
  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-lab">
        <header className="page-header">
          <div>
            <p className="eyebrow">Synthetic failure laboratory</p>
            <h1>Prove what survives an ambiguous response</h1>
          </div>
          <p>
            Run a deterministic provider fault, then inspect the immutable operation, ledger,
            event, and delivery evidence produced by recovery.
          </p>
        </header>
        <ScenarioLab csrfToken={session.csrfToken} />
      </main>
    </ConsoleShell>
  );
}
