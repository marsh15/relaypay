import type { Evidence } from "@/lib/evidence-types";
import { EvidenceSection, Money } from "./shared";

export function LedgerSection({ evidence }: { evidence: Evidence }) {
  const debits = evidence.ledger.postings
    .filter((item) => item.side === "DEBIT")
    .reduce((sum, item) => sum + item.amount, 0);
  const credits = evidence.ledger.postings
    .filter((item) => item.side === "CREDIT")
    .reduce((sum, item) => sum + item.amount, 0);
  return (
    <EvidenceSection
      id="ledger"
      title="Immutable ledger"
      intro="Posted journals and account-labelled debit/credit evidence; balances derive from postings."
    >
      {evidence.ledger.journals.length ? (
        <>
          <div className="invariant-banner">
            <span aria-hidden="true">{debits === credits ? "✓" : "!"}</span>
            <strong>{debits === credits ? "Debits equal credits" : "Ledger requires inspection"}</strong>
            <span>
              <Money paise={debits} /> debit · <Money paise={credits} /> credit
            </span>
          </div>
          <div className="table-scroll" tabIndex={0} aria-label="Scrollable ledger postings table">
            <table>
              <caption>Immutable postings for {evidence.ledger.journals.length} journal</caption>
              <thead>
                <tr>
                  <th scope="col">Journal</th>
                  <th scope="col">Account</th>
                  <th scope="col">Side</th>
                  <th scope="col" className="numeric">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {evidence.ledger.postings.map((posting, index) => (
                  <tr key={`${posting.journalId}-${posting.accountCode}-${index}`}>
                    <td>
                      <code>{posting.journalId}</code>
                    </td>
                    <td>{posting.accountCode.replaceAll("_", " ")}</td>
                    <td>{posting.side}</td>
                    <td className="numeric">
                      <Money paise={posting.amount} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="section-empty">Authorization creates no journal. Ledger evidence appears after capture.</p>
      )}
    </EvidenceSection>
  );
}
