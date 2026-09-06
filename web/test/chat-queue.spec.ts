import { test, expect, Page } from "@playwright/test";

async function setupChat(page: Page) {
  const state = {
    messages: [] as string[],
    busy: false,
    historyVersion: 0,
    history: [
      { role: "User", content: "Earlier" },
      { role: "Assistant", content: "Hello" },
    ],
    queued: [] as { turn_id: number; message: string }[],
  };
  await page.clock.install();
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/chat") {
      const body = route.request().postDataJSON();
      state.messages.push(body.message);
      state.busy = true;
      await route.fulfill({
        json: { status: "ok", trigger_gen: 11, turn_id: state.messages.length },
      });
    } else if (path === "/api/poll") {
      await route.fulfill({
        json: {
          artifacts: {},
          history_version: state.historyVersion,
          chat_busy: state.busy,
          queued_messages: state.queued,
          // An interrupted old pipeline left a counter deficit.
          pipeline_triggers: 11,
          pipeline_finishes: 10,
          running_agents: state.busy ? { main: 1 } : {},
        },
      });
    } else if (path === "/api/history") {
      await route.fulfill({ json: { history: state.history } });
    } else if (path === "/api/health") {
      await route.fulfill({ json: { status: "ok", user_role: "User" } });
    } else if (path === "/api/artifacts") {
      await route.fulfill({ json: { artifacts: {}, version_counts: {} } });
    } else {
      await route.fulfill({ json: { agents: [] } });
    }
  });
  await page.goto("/");
  await expect(page.locator("#messages")).toContainText("Hello");
  return state;
}

test("Send accepts more messages while the full pipeline runs", async ({
  page,
}) => {
  const state = await setupChat(page);
  const input = page.locator("#user-input");
  const send = page.locator("#send-btn");
  await input.fill("first");
  await send.click();
  await expect(send).toBeEnabled();

  state.history.push({ role: "User", content: "first" });
  state.historyVersion++;
  await page.clock.fastForward(2000);
  await expect(page.locator("#messages")).toContainText("first");
  await expect(send).toBeEnabled();
  await expect(page.locator("#delete-btn")).toBeDisabled();
  await expect(page.locator("#reroll-btn")).toBeDisabled();

  await input.fill("second");
  await send.click();
  await expect(send).toBeEnabled();
  await input.fill("third");
  await input.press("Enter");
  await expect(send).toBeEnabled();
  expect(state.messages).toEqual(["first", "second", "third"]);

  state.queued = [
    { turn_id: 2, message: "second" },
    { turn_id: 3, message: "third" },
  ];
  await page.clock.fastForward(2000);
  await expect(page.locator("#chat-queue")).toHaveText(
    "Queued: second\nQueued: third",
  );

  state.busy = false;
  state.queued = [];
  await page.clock.fastForward(2000);
  // Old aggregate counters remain unbalanced, but runtime work is finished.
  await expect(page.locator("#delete-btn")).toBeEnabled();
  await expect(page.locator("#reroll-btn")).toBeEnabled();
  await expect(page.locator("#chat-queue")).toBeEmpty();
});

test("Polling cannot unlock Send before the submission response arrives", async ({
  page,
}) => {
  const state = await setupChat(page);
  let accept: (() => void) | undefined;
  await page.route("**/api/chat", async (route) => {
    await new Promise<void>((resolve) => {
      accept = resolve;
    });
    state.busy = true;
    await route.fulfill({ json: { status: "ok", turn_id: 1 } });
  });
  await page.locator("#user-input").fill("pending request");
  await page.locator("#send-btn").click();
  await expect.poll(() => !!accept).toBe(true);
  await page.clock.fastForward(2000);
  await expect(page.locator("#send-btn")).toBeDisabled();
  accept!();
  await expect(page.locator("#send-btn")).toBeEnabled();
});

for (const failure of ["http", "network", "invalid-json"]) {
  test(`Failed submission releases Send and preserves the draft: ${failure}`, async ({
    page,
  }) => {
    await setupChat(page);
    await page.route("**/api/chat", async (route) => {
      if (failure === "network") await route.abort();
      else if (failure === "invalid-json")
        await route.fulfill({ body: "not JSON" });
      else
        await route.fulfill({
          status: 500,
          json: { error: "Failed to save state" },
        });
    });
    const dialogs: string[] = [];
    page.on("dialog", async (dialog) => {
      dialogs.push(dialog.message());
      await dialog.accept();
    });
    await page.locator("#user-input").fill("keep this draft");
    await page.locator("#send-btn").click();
    await expect(page.locator("#send-btn")).toBeEnabled();
    await expect(page.locator("#user-input")).toHaveValue("keep this draft");
    await expect(page.locator("#messages")).not.toContainText(
      "keep this draft",
    );
    expect(dialogs).toHaveLength(1);
  });
}
