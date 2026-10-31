import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

async function signIn(page: import("@playwright/test").Page, next: string) {
  await page.goto(`/login?next=${next}`);
  await page.getByLabel("Administrator email").fill("admin@northstar.test");
  await page.getByLabel("Password").fill("RelayPay-Northstar-2026!");
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("administrator filters payments by status and opens the evidence trail", async ({ page }) => {
  await signIn(page, "/lab");
  await expect(page).toHaveURL(/\/lab$/);
  // Creates one captured payment (with its journal) in the synthetic sandbox.
  await page.getByRole("button", { name: "Run lost-response scenario" }).click();
  await expect(page.getByText("Lost-response scenario verified successfully")).toBeVisible();

  await page.getByRole("link", { name: "Payments", exact: true }).click();
  await expect(page).toHaveURL(/\/payments(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: "Payments", level: 1 })).toBeVisible();

  await page.getByLabel("Status", { exact: true }).selectOption("captured");
  await page.getByRole("button", { name: "Apply filter" }).click();
  await expect(page).toHaveURL(/status=captured/);
  const table = page.getByRole("table", { name: "Payment intents, newest first" });
  await expect(table).toBeVisible();
  await expect(table.locator(".state-badge").first()).toBeVisible();
  const badges = await table.locator(".state-badge").allTextContents();
  expect(badges.length).toBeGreaterThan(0);
  expect(badges.every((text) => text.includes("captured"))).toBe(true);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);

  await table.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/payments\/pay_[0-9a-f]{32}$/);
  await expect(page.getByRole("heading", { name: "Trace the committed proof" })).toBeVisible();

  await page.setViewportSize({ width: 320, height: 900 });
  const mobileAccessibility = await new AxeBuilder({ page }).analyze();
  expect(mobileAccessibility.violations).toEqual([]);
});

test("administrator drills from ledger journals to balanced postings", async ({ page }) => {
  await signIn(page, "/ledger");
  await expect(page).toHaveURL(/\/ledger(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: "Ledger journals", level: 1 })).toBeVisible();

  const table = page.getByRole("table", { name: "Posted journals, newest first" });
  await expect(table).toBeVisible();
  await table.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/ledger\/jrn_[0-9a-f]{32}\?environment=/);
  await expect(page.getByRole("heading", { name: /journal$/, level: 1 })).toBeVisible();
  await expect(page.getByText("Debits equal credits")).toBeVisible();
  await expect(page.getByRole("table", { name: /Postings for journal/ })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);

  await page.getByRole("link", { name: "← Ledger journals" }).click();
  await expect(page.getByRole("heading", { name: "Ledger journals", level: 1 })).toBeVisible();

  await page.setViewportSize({ width: 320, height: 900 });
  const mobileAccessibility = await new AxeBuilder({ page }).analyze();
  expect(mobileAccessibility.violations).toEqual([]);
});
