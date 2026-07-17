"use client";

import { useState } from "react";

export function SafeJson({ value }: { value: unknown }) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(value, null, 2);

  async function copy() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <details className="safe-json">
      <summary>Raw sanitized evidence</summary>
      <div className="code-toolbar">
        <span>Secrets, cookies, raw headers, and stored response bytes are excluded.</span>
        <button className="button button-code" type="button" onClick={copy}>
          {copied ? "Copied" : "Copy JSON"}
        </button>
      </div>
      <pre tabIndex={0}>
        <code>{text}</code>
      </pre>
      <p className="sr-only" aria-live="polite">
        {copied ? "Sanitized evidence copied" : ""}
      </p>
    </details>
  );
}
