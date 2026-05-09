import { test, expect } from "@playwright/test";

test("UI loads without JavaScript errors", async ({ page }) => {
  const pageErrors: Error[] = [];
  // Catch any JS exceptions that halt execution
  page.on("pageerror", (error) => pageErrors.push(error));

  await page.goto("/");

  // If pageErrors isn't empty, the test fails immediately
  expect(pageErrors).toHaveLength(0);

  // Make sure the main UI actually rendered
  await expect(page.locator("#send-btn")).toBeVisible();
});
