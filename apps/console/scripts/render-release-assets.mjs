import fs from "node:fs";
import path from "node:path";

import { chromium } from "playwright";

const outputDir = process.argv[2];
if (!outputDir) throw new Error("output directory argument is required");
fs.mkdirSync(outputDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const baseStyles = `
  * { box-sizing: border-box; }
  body { margin: 0; width: 1440px; height: 900px; overflow: hidden; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
`;

async function card(name, body) {
  await page.setContent(`<style>${baseStyles}</style>${body}`);
  await page.screenshot({ path: path.join(outputDir, name), omitBackground: true });
}

await card(
  "title.png",
  `<main style="height:100%;padding:96px;background:#0b1020;color:#f8fafc;display:grid;grid-template-rows:1fr auto;">
    <section style="align-self:center;text-align:center;">
      <div style="font-size:24px;letter-spacing:.2em;text-transform:uppercase;color:#8ee3cf;">Evidence-first orchestration</div>
      <h1 style="font-size:76px;line-height:1;margin:28px 0 18px;">RelayPay v0.1.0</h1>
      <p style="font-size:34px;color:#cbd5e1;margin:0;">Exactly-once recovery after a lost provider response</p>
    </section>
    <section style="display:flex;align-items:center;justify-content:center;gap:24px;font-size:22px;">
      <span style="padding:20px 30px;border:1px solid #334155;border-radius:16px;">Merchant API</span><b style="color:#8ee3cf;">→</b>
      <span style="padding:20px 30px;border:1px solid #8ee3cf;border-radius:16px;">PostgreSQL authority</span><b style="color:#8ee3cf;">↔</b>
      <span style="padding:20px 30px;border:1px solid #334155;border-radius:16px;">Provider lookup</span><b style="color:#8ee3cf;">→</b>
      <span style="padding:20px 30px;border:1px solid #334155;border-radius:16px;">Signed webhook</span>
    </section>
  </main>`,
);

await card(
  "end.png",
  `<main style="height:100%;padding:96px;background:#0b1020;color:#f8fafc;display:grid;place-content:center;text-align:center;">
    <div style="font-size:64px;font-weight:750;">Synthetic INR data only</div>
    <p style="font-size:34px;color:#8ee3cf;margin:28px 0 12px;">Not a payment processor</p>
    <p style="font-size:27px;color:#cbd5e1;margin:0;">Never use real financial, identity, or customer data.</p>
  </main>`,
);

const captions = [
  "Run the deterministic lost-response scenario.",
  "Recover by signed lookup—never repeat a recorded mutation.",
  "Verify one effect, balanced journal, stable replay, event, and delivery.",
];
for (const [index, caption] of captions.entries()) {
  await card(
    `caption-${index + 1}.png`,
    `<main style="height:100%;background:transparent;position:relative;">
      <div style="position:absolute;left:140px;right:140px;bottom:38px;padding:18px 28px;border-radius:14px;background:rgba(5,10,24,.9);border:1px solid rgba(142,227,207,.6);color:#f8fafc;text-align:center;font-size:27px;line-height:1.3;box-shadow:0 12px 40px rgba(0,0,0,.4);">${caption}</div>
    </main>`,
  );
}

await browser.close();
