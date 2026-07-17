"use client";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main-content" className="centered-state">
      <p className="eyebrow">Known state preserved</p>
      <h1>Evidence is temporarily unavailable</h1>
      <p>The console could not refresh this view. No financial state has been labelled failed.</p>
      <button className="button button-primary" type="button" onClick={reset}>
        Retry evidence load
      </button>
    </main>
  );
}
