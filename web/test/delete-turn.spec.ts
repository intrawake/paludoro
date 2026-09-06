import { test, expect } from "@playwright/test";

type HistoryEntry = { role: string; content: string; raw_content: string };

const entry = (role: string, content: string): HistoryEntry => ({
  role,
  content,
  raw_content: content,
});

test("delete last turn uses the causal rollback endpoint", async ({ page }) => {
  const originalHistory = [
    entry("User", "hello"),
    entry("Ritika", "hi"),
    entry("User", "sleep"),
    entry("Ritika", "good night"),
  ];
  const rolledBackHistory = originalHistory.slice(0, 2);
  let deleted = false;
  let deleteRequests = 0;

  await page.route("**/api/history/last-turn", async (route) => {
    deleteRequests += 1;
    expect(route.request().method()).toBe("DELETE");
    deleted = true;
    await route.fulfill({
      json: {
        status: "ok",
        deleted_turn_id: 2,
        history: rolledBackHistory,
        artifacts: { "mood.sxpb": "(mood happy)" },
        version_counts: { "mood.sxpb": 2 },
      },
    });
  });

  await page.route("**/api/history", async (route) => {
    await route.fulfill({
      json: { history: deleted ? rolledBackHistory : originalHistory },
    });
  });
  await page.route("**/api/health", async (route) => {
    await route.fulfill({ json: { status: "ok", user_role: "User" } });
  });
  await page.route("**/api/poll*", async (route) => {
    await route.fulfill({
      json: {
        artifacts: {},
        history_version: deleted ? 2 : 1,
        running_agents: {},
        pipeline_triggers: 0,
        pipeline_finishes: 0,
      },
    });
  });
  await page.route("**/api/agents", async (route) => {
    await route.fulfill({ json: { agents: [] } });
  });
  await page.route("**/api/artifacts", async (route) => {
    await route.fulfill({
      json: deleted
        ? {
            artifacts: { "mood.sxpb": "(mood happy)" },
            version_counts: { "mood.sxpb": 2 },
          }
        : {
            artifacts: {
              "mood.sxpb": "(mood sleepy)",
              "image.png": "/images/latest.png",
            },
            version_counts: { "mood.sxpb": 3, "image.png": 1 },
          },
    });
  });
  await page.route("**/api/artifacts/**", async (route) => {
    await route.fulfill({ json: { versions: [] } });
  });

  await page.goto("/");
  await expect(page.locator("#messages > div")).toHaveCount(4);

  await page.click("#more-btn");
  await expect(page.locator("#delete-btn")).toBeVisible();
  page.on("dialog", (dialog) => dialog.accept());
  await page.click("#delete-btn");

  await expect(page.locator("#messages > div")).toHaveCount(2);
  await expect(page.locator("#artifact-list")).toContainText("mood.sxpb");
  await expect(page.locator("#artifact-list")).not.toContainText("image.png");
  expect(deleteRequests).toBe(1);
});
