import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { Money } from "@/components/evidence/shared";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Journal postings" };

type Environment = { id: string; name: string; type: string; status: string };
type Posting = {
  accountCode: string;
  accountName: string;
  side: "DEBIT" | "CREDIT";
  amount: number;
  currency: string;
};
type JournalDetail = {
  id: string;
  journalType: string;
  referenceType: string;
  currency: string;
  postedAt: string;
  debitTotal: number;
  creditTotal: number;
  balanced: boolean;
  postings: Posting[];
};

export default async function JournalPostings({
  params,
  searchParams,
}: {
  params: Promise<{ journalId: string }>;
  searchParams: Promise<{ environment?: string }>;
}) {
  const { journalId } = await params;
  const session = await getSession();
  if (!session) redirect(`/login?next=/ledger/${encodeURIComponent(journalId)}`);
  const query = await searchParams;
  const environmentsResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentsResponse.ok
    ? ((await environmentsResponse.json()) as Environment[])
    : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];
  if (!environment) notFound();

  const response = await backendFetch(
    `/api/admin/v1/environments/${encodeURIComponent(environment.id)}/journals/${encodeURIComponent(journalId)}`,
  );
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error("Journal postings are temporarily unavailable.");
  const journal = (await response.json()) as JournalDetail;

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-evidence">
        <header className="evidence-page-header">
          <div>
            <Link className="back-link" href={`/ledger?environment=${encodeURIComponent(environment.id)}`}>
              ← Ledger journals
            </Link>
            <p className="eyebrow">Journal postings</p>
            <h1>{journal.journalType.replaceAll("_", " ").toLowerCase()} journal</h1>
          </div>
          <dl>
            <div>
              <dt>Journal</dt>
              <dd>{journal.id}</dd>
            </div>
            <div>
              <dt>Reference</dt>
              <dd>{journal.referenceType.replaceAll("_", " ")}</dd>
            </div>
          </dl>
        </header>

        <section className="evidence-section" aria-labelledby="postings-title">
          <header className="evidence-section-heading">
            <h2 id="postings-title">Postings</h2>
            <p>Every posting for this journal, with its debit and credit totals.</p>
          </header>
          <div className="invariant-banner" role="status">
            <span aria-hidden="true">{journal.balanced ? "✓" : "!"}</span>
            <strong>{journal.balanced ? "Debits equal credits" : "Journal requires inspection"}</strong>
            <span>
              <Money paise={journal.debitTotal} /> debit · <Money paise={journal.creditTotal} /> credit
            </span>
          </div>
          <div className="table-scroll" tabIndex={0} aria-label="Scrollable journal postings table">
            <table>
              <caption>Postings for journal {journal.id}</caption>
              <thead>
                <tr>
                  <th scope="col">Account</th>
                  <th scope="col">Side</th>
                  <th scope="col" className="numeric">Amount</th>
                </tr>
              </thead>
              <tbody>
                {journal.postings.map((posting, index) => (
                  <tr key={`${posting.accountCode}-${posting.side}-${index}`}>
                    <td>
                      {posting.accountName}
                      <div>
                        <code>{posting.accountCode}</code>
                      </div>
                    </td>
                    <td>{posting.side}</td>
                    <td className="numeric">
                      <Money paise={posting.amount} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <SafeJson value={journal} />
      </main>
    </ConsoleShell>
  );
}
