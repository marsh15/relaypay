import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("administrator proves and inspects one lost capture response", async ({ page }) => {
  await page.goto("/login?next=//untrusted.example");
  await page.getByLabel("Administrator email").fill("admin@northstar.test");
  await page.getByLabel("Password").fill("RelayPay-Northstar-2026!");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/lab$/);
  await page.getByRole("button", { name: "Run lost-response scenario" }).click();
  await expect(page.getByText("Lost-response scenario verified successfully")).toBeVisible();

  const assertions = page.getByLabel("Verified invariants");
  await expect(assertions.getByText("One provider capture effect")).toBeVisible();
  await expect(assertions.getByText("One acknowledged webhook")).toBeVisible();
  await expect(assertions.locator(".assertion-mismatch")).toHaveCount(0);

  await page.getByRole("link", { name: "Inspect payment evidence" }).click();
  await expect(page.getByRole("heading", { name: "Verified: one provider capture effect" })).toBeVisible();
  await expect(page.getByText("Capture terminal response digests are byte-identical.")).toBeVisible();
  await expect(page.getByText("Debits equal credits")).toBeVisible();
  await expect(page.locator(".state-badge", { hasText: "DELIVERED" })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
