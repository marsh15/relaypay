import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("administrator runs merchant risk reviews with hard-stop escalations", async ({ page }) => {
  await page.goto("/login?next=/risk");
  await page.getByLabel("Administrator email").fill("admin@northstar.test");
  await page.getByLabel("Password").fill("RelayPay-Northstar-2026!");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/^https?:\/\/[^/]+\/risk(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: "Merchant risk review" })).toBeVisible();

  await page.getByLabel("Merchant site snapshot").selectOption("SUSPICIOUS_CLAIMS");
  await page.getByRole("button", { name: "Run risk review" }).click();
  await expect(page).toHaveURL(/\/risk\/rsk_[0-9a-f]{32}/);
  await expect(page.getByRole("heading", { name: "Score breakdown" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible();
  await expect(page.getByText("guaranteed return").first()).toBeVisible();
  await expect(page.getByText("escalation", { exact: false }).first()).toBeVisible();

  // Reviewers annotate without rewriting immutable evidence.
  await page.getByLabel("Annotation").fill("Synthetic reviewer follow-up requested.");
  await page.getByRole("button", { name: "Add annotation" }).click();
  await expect(page.getByText("Recorded. Immutable evidence, findings, and scores are unchanged.")).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);

  await page.setViewportSize({ width: 320, height: 900 });
  const mobileAccessibility = await new AxeBuilder({ page }).analyze();
  expect(mobileAccessibility.violations).toEqual([]);
});
