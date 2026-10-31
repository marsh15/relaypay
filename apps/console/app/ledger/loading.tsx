export default function LedgerLoading() {
  return (
    <main id="main-content" className="page page-operations" aria-busy="true">
      <p className="eyebrow">Immutable double-entry ledger</p>
      <h1>Loading ledger journals…</h1>
      <div className="operations-loading" role="status" aria-label="Loading ledger journals">
        <div /><div /><div />
      </div>
    </main>
  );
}
