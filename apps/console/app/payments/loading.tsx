export default function PaymentsLoading() {
  return (
    <main id="main-content" className="page page-operations" aria-busy="true">
      <p className="eyebrow">Tenant-scoped payment intents</p>
      <h1>Loading payments…</h1>
      <div className="operations-loading" role="status" aria-label="Loading payments">
        <div /><div /><div />
      </div>
    </main>
  );
}
