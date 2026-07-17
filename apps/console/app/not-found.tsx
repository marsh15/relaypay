import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main-content" className="centered-state">
      <p className="eyebrow">Evidence unavailable</p>
      <h1>Resource not found</h1>
      <p>The record does not exist or is not available to this organisation.</p>
      <Link className="button button-primary" href="/lab">
        Return to Scenario Lab
      </Link>
    </main>
  );
}
