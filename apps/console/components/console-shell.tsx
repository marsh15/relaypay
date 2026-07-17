import type { ReactNode } from "react";
import Link from "next/link";
import type { Session } from "@/lib/types";
import { LogoutButton } from "./logout-button";

export function ConsoleShell({ session, children }: { session: Session; children: ReactNode }) {
  return (
    <div className="console-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <Link className="compact-wordmark" href="/lab" aria-label="RelayPay Scenario Lab">
            RelayPay
          </Link>
          <nav aria-label="Primary navigation">
            <Link className="nav-link" href="/lab">
              Scenario Lab
            </Link>
          </nav>
          <div className="account-cluster">
            <span className="environment-badge">Synthetic sandbox</span>
            <span className="organisation-badge">{session.organisationId}</span>
            <LogoutButton csrfToken={session.csrfToken} />
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
