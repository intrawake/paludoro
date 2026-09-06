import { test, expect } from "@playwright/test";

/**
 * Reroll E2E tests.
 *
 * We focus on verifying that reroll sends the correct request to the server.
 * The poll dedup logic in the app relies on server-side `requestedLen` tracking,
 * which is hard to simulate in a mock. So we:
 * 1. Pre-seed conversationHistory via the /api/history mock (avoids race with send+poll).
 * 2. Intercept /api/chat to verify reroll requests.
 * 3. Use { force: true } for reroll clicks since the button is behind "More".
 */

type HistoryEntry = { role: string; content: string; raw_content: string };

let chatRequests: any[] = [];

function resetState() {
  chatRequests = [];
}

function entry(role: string, content: string): HistoryEntry {
  return { role, content, raw_content: content };
}

function setupRoutes(
  page: any,
  opts: { initialHistory?: HistoryEntry[] } = {},
) {
  const initialHistory = opts.initialHistory ?? [];

  page.route("**/api/history", async (route: any) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { history: initialHistory } });
    } else {
      await route.fulfill({ json: { status: "ok" } });
    }
  });

  page.route("**/api/health", async (route: any) => {
    await route.fulfill({ json: { status: "ok", user_role: "User" } });
  });

  // Poll: return nothing (we test reroll requests, not poll dedup)
  page.route("**/api/poll*", async (route: any) => {
    await route.fulfill({
      json: { artifacts: {}, new_history: [], running_agents: [] },
    });
  });

  // Chat: record all requests
  page.route("**/api/chat", async (route: any) => {
    const body = route.request().postDataJSON();
    chatRequests.push(body);
    await route.fulfill({ json: { status: "ok" } });
  });

  page.route("**/api/agents", async (route: any) => {
    await route.fulfill({ json: { agents: [], available: [] } });
  });
  page.route("**/api/artifacts", async (route: any) => {
    await route.fulfill({ json: { artifacts: {}, version_counts: {} } });
  });
  page.route("**/api/artifacts/**", async (route: any) => {
    await route.fulfill({ json: { versions: [] } });
  });
}

async function revealReroll(page: any) {
  const moreBtn = page.locator("#more-btn");
  if (await moreBtn.isVisible()) {
    await moreBtn.click();
  }
  await expect(page.locator("#reroll-btn")).toBeVisible();
}

test.describe("Reroll", () => {
  test.beforeEach(({ page }) => {
    resetState();
  });

  test("reroll with assistant last: pops message and sends reroll request", async ({
    page,
  }) => {
    setupRoutes(page, {
      initialHistory: [entry("User", "hello"), entry("Alice", "hi there")],
    });

    await page.goto("/");
    await page.waitForTimeout(500);
    await revealReroll(page);

    // Should have 2 messages loaded from history
    await expect(page.locator("#messages > div")).toHaveCount(2);

    // Click reroll
    await page.click("#reroll-btn");
    await page.waitForTimeout(300);

    // Should have popped the assistant message locally
    await expect(page.locator("#messages > div")).toHaveCount(1);

    // Should have sent exactly 1 reroll request
    const rerolls = chatRequests.filter((r: any) => r.reroll);
    expect(rerolls).toHaveLength(1);
    expect(rerolls[0].reroll).toBe(true);
    // No message or history sent — server reads history from its own state
    expect(rerolls[0].message).toBeUndefined();
  });

  test("reroll with user last (retry after failure): sends reroll without popping", async ({
    page,
  }) => {
    setupRoutes(page, {
      initialHistory: [entry("User", "hello")],
    });

    await page.goto("/");
    await page.waitForTimeout(500);
    await revealReroll(page);

    // Should have 1 message
    await expect(page.locator("#messages > div")).toHaveCount(1);

    // Click reroll — must work even though last message is from user
    await page.click("#reroll-btn");
    await page.waitForTimeout(300);

    // Nothing should have been popped
    await expect(page.locator("#messages > div")).toHaveCount(1);

    // Should have sent exactly 1 reroll request
    const rerolls = chatRequests.filter((r: any) => r.reroll);
    expect(rerolls).toHaveLength(1);
    expect(rerolls[0].reroll).toBe(true);
    expect(rerolls[0].message).toBeUndefined();
  });

  test("rapid reroll clicks only send one request", async ({ page }) => {
    setupRoutes(page, {
      initialHistory: [entry("User", "hello"), entry("Alice", "hi")],
    });

    await page.goto("/");
    await page.waitForTimeout(500);
    await revealReroll(page);

    // Click reroll
    await page.click("#reroll-btn");

    // Rapidly click again (force bypasses the disabled state)
    const rerollBtn = page.locator("#reroll-btn");
    await rerollBtn.click({ force: true });
    await rerollBtn.click({ force: true });
    await rerollBtn.click({ force: true });

    await page.waitForTimeout(500);

    // Only 1 reroll should have been sent (isThinking blocks subsequent clicks)
    const rerolls = chatRequests.filter((r: any) => r.reroll);
    expect(rerolls).toHaveLength(1);
  });

  test("empty history: reroll is a no-op", async ({ page }) => {
    setupRoutes(page, { initialHistory: [] });

    await page.goto("/");
    await page.waitForTimeout(500);
    await revealReroll(page);

    // Reroll should be disabled when history is empty
    await expect(page.locator("#reroll-btn")).toBeDisabled();
  });

  test("UI recovers when pipeline finishes without producing history", async ({
    page,
  }) => {
    // This tests the pipeline failure recovery: server reports pipeline finished
    // but no new history entries. The UI should unstick.
    let pipelineTriggers = 0;
    let pipelineFinishes = 0;
    let chatBusy = false;

    page.route("**/api/history", async (route: any) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: { history: [entry("User", "hello")] } });
      } else {
        await route.fulfill({ json: { status: "ok" } });
      }
    });

    page.route("**/api/health", async (route: any) => {
      await route.fulfill({ json: { status: "ok", user_role: "User" } });
    });

    // Poll returns pipeline state (simulates pipeline that finished but failed)
    page.route("**/api/poll*", async (route: any) => {
      await route.fulfill({
        json: {
          artifacts: {},
          new_history: [],
          running_agents: [],
          pipeline_triggers: pipelineTriggers,
          pipeline_finishes: pipelineFinishes,
          chat_busy: chatBusy,
          queued_messages: [],
        },
      });
    });

    // Chat returns trigger_gen (simulates pipeline being scheduled)
    page.route("**/api/chat", async (route: any) => {
      pipelineTriggers += 1;
      chatBusy = true;
      await route.fulfill({
        json: { status: "ok", trigger_gen: pipelineTriggers },
      });
    });

    page.route("**/api/agents", async (route: any) => {
      await route.fulfill({ json: { agents: [], available: [] } });
    });
    page.route("**/api/artifacts", async (route: any) => {
      await route.fulfill({ json: { artifacts: {}, version_counts: {} } });
    });
    page.route("**/api/artifacts/**", async (route: any) => {
      await route.fulfill({ json: { versions: [] } });
    });

    await page.goto("/");
    await page.waitForTimeout(500);
    await revealReroll(page);

    // Send button should be enabled initially
    await expect(page.locator("#send-btn")).toBeEnabled();

    // Click reroll
    await page.click("#reroll-btn");

    // New messages can queue, but reroll must wait for the current turn.
    await expect(page.locator("#send-btn")).toBeEnabled();
    await expect(page.locator("#reroll-btn")).toBeDisabled();

    // Simulate pipeline finishing (without producing history)
    pipelineFinishes = pipelineTriggers;
    chatBusy = false;

    // Runtime state, not history output, releases destructive controls.
    await expect(page.locator("#reroll-btn")).toBeEnabled({ timeout: 5000 });
  });
});
