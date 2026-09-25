import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("administrator asks settlement questions with cited deterministic answers", async ({ page }) => {
  await page.goto("/login?next=/settlement");
  await page.getByLabel("Administrator email").fill("admin@northstar.test");
  await page.getByLabel("Password").fill("RelayPay-Northstar-2026!");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/^https?:\/\/[^/]+\/settlement(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: "Settlement intelligence" })).toBeVisible();
  await expect(
    page.getByText(/Asia\/Kolkata · cutoff 17:00 · T\+1 · weekends skipped/),
  ).toBeVisible();

  await page.getByRole("button", { name: "Which payments are still unsettled?" }).click();
  await expect(page).toHaveURL(/\/settlement\/sqn_[0-9a-f]{32}/);
  await expect(page.getByRole("heading", { name: /Which payments are still unsettled\?/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Calculation steps" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Citations" })).toBeVisible();
  await expect(page.getByText(/settlement policy/).first()).toBeVisible();

  await page.getByRole("link", { name: "Settlement" }).click();
  await expect(page.getByRole("heading", { name: "Questions" })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("unsupported settlement question returns a typed clarification", async ({ page }) => {
  await page.goto("/login?next=/settlement");
  await page.getByLabel("Administrator email").fill("admin@northstar.test");
  await page.getByLabel("Password").fill("RelayPay-Northstar-2026!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/^https?:\/\/[^/]+\/settlement(?:\?.*)?$/);

  await page.getByLabel("Question").fill("What is the capital of France?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page).toHaveURL(/\/settlement\/sqn_[0-9a-f]{32}/);
  await expect(page.getByRole("heading", { name: "Rephrase the question" })).toBeVisible();
  await expect(page.getByText(/four supported settlement questions/)).toBeVisible();

  await page.setViewportSize({ width: 320, height: 900 });
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
