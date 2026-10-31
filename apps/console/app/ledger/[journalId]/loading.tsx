export default function JournalPostingsLoading() {
  return (
    <main id="main-content" className="page page-evidence" aria-busy="true">
      <p className="eyebrow">Journal postings</p>
      <h1>Loading journal postings…</h1>
      <div className="operations-loading" role="status" aria-label="Loading journal postings">
        <div /><div /><div />
      </div>
    </main>
  );
}
