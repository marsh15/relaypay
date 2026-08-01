export default function OperationsLoading() {
  return (
    <main id="main-content" className="page page-operations" aria-busy="true">
      <p className="eyebrow">Tenant-scoped control plane</p>
      <h1>Loading operations workspace…</h1>
      <div className="operations-loading" role="status" aria-label="Loading operational records">
        <div /><div /><div />
      </div>
    </main>
  );
}
