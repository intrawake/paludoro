import { test, expect } from "@playwright/test";

// Mock API state
let mockArtifacts: Record<string, string> = {};
let mockArtifactVersions: Record<string, [string, number][]> = {};

function resetMockState() {
  mockArtifacts = {};
  mockArtifactVersions = {};
}

function setupMockRoutes(page: any) {
  // Mock history endpoint (empty)
  page.route("**/api/history", async (route: any) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { history: [] } });
    } else {
      await route.fulfill({ json: { status: "ok" } });
    }
  });

  // Mock health endpoint
  page.route("**/api/health", async (route: any) => {
    await route.fulfill({ json: { status: "ok", user_role: "User" } });
  });

  // Mock poll endpoint (empty)
  page.route("**/api/poll*", async (route: any) => {
    await route.fulfill({
      json: { artifacts: {}, new_history: [], running_agents: [] },
    });
  });

  // Mock agents endpoint
  page.route("**/api/agents", async (route: any) => {
    await route.fulfill({ json: { agents: [] } });
  });

  // Mock artifact sub-routes (versions, PUT, DELETE)
  page.route("**/api/artifacts/**", async (route: any) => {
    const method = route.request().method();
    const url = route.request().url();
    const afterPrefix = url.split("/api/artifacts/")[1];

    // GET /api/artifacts/{artipath}/versions
    if (method === "GET" && afterPrefix.endsWith("/versions")) {
      const artipath = decodeURIComponent(
        afterPrefix.slice(0, -"/versions".length),
      );
      const versions = mockArtifactVersions[artipath] || [];
      await route.fulfill({
        json: { artipath, versions },
      });
      return;
    }

    // PUT /api/artifacts/{artipath}
    if (method === "PUT") {
      const artipath = decodeURIComponent(afterPrefix);
      const body = await route.request().postDataJSON();
      const newContent = body.content;

      mockArtifacts[artipath] = newContent;
      if (!mockArtifactVersions[artipath]) {
        mockArtifactVersions[artipath] = [];
      }
      mockArtifactVersions[artipath].push([
        newContent,
        mockArtifactVersions[artipath].length,
      ]);

      await route.fulfill({ json: { status: "ok" } });
      return;
    }

    // DELETE /api/artifacts/{artipath}/versions/{index}
    if (method === "DELETE") {
      const parts = afterPrefix.split("/versions/");
      const artipath = decodeURIComponent(parts[0]);
      const versionIndex = parseInt(parts[1], 10);

      if (
        mockArtifactVersions[artipath] &&
        mockArtifactVersions[artipath].length > 1
      ) {
        mockArtifactVersions[artipath].splice(versionIndex, 1);
        mockArtifacts[artipath] =
          mockArtifactVersions[artipath][
            mockArtifactVersions[artipath].length - 1
          ][0];
        await route.fulfill({
          json: {
            status: "ok",
            artifact_versions: mockArtifactVersions,
          },
        });
      } else {
        await route.fulfill({
          status: 400,
          json: { error: "Cannot delete the last version" },
        });
      }
      return;
    }

    await route.fallback();
  });

  // Mock artifacts list endpoint (GET, eager current content, no base64)
  page.route("**/api/artifacts", async (route: any) => {
    // Strip base64 from artifacts for the list response
    const cleanArtifacts: Record<string, string> = {};
    for (const [name, content] of Object.entries(mockArtifacts)) {
      if (
        typeof content === "string" &&
        content.startsWith("data:image/png;base64,")
      ) {
        cleanArtifacts[name] = "/images/mock.png";
      } else {
        cleanArtifacts[name] = content;
      }
    }
    const versionCounts: Record<string, number> = {};
    for (const name of Object.keys(mockArtifacts)) {
      versionCounts[name] = (mockArtifactVersions[name] || []).length || 1;
    }
    await route.fulfill({
      json: { artifacts: cleanArtifacts, version_counts: versionCounts },
    });
  });
}

async function loadArtifactFixture(page: any) {
  resetMockState();

  // Set up a text artifact with 2 versions
  mockArtifacts["test.sxpb"] = "(name Test)\n(value original)";
  mockArtifactVersions["test.sxpb"] = [
    ["(name Test)\n(value v1)", 0],
    ["(name Test)\n(value v2)", 1],
  ];

  await setupMockRoutes(page);
  await page.goto("/");

  // Wait for sidebar to populate
  await page.waitForTimeout(500);

  // Click the artifact in sidebar
  await page.locator(".artifact-item").first().click();

  // Wait for versions to load
  await page.waitForTimeout(500);
}

test.describe("Artifact Edit Mode", () => {
  test("Edit button appears for text artifacts", async ({ page }) => {
    await loadArtifactFixture(page);

    const editBtn = page.locator("#edit-artifact-btn");
    await expect(editBtn).toBeVisible();
  });

  test("Clicking Edit enters textarea mode", async ({ page }) => {
    await loadArtifactFixture(page);

    await page.locator("#edit-artifact-btn").click();

    const textarea = page.locator("#artifact-editor");
    await expect(textarea).toBeVisible();
    await expect(textarea).toHaveValue("(name Test)\n(value v2)");

    // Save/Cancel visible, Edit/Delete hidden
    await expect(page.locator("#save-artifact-btn")).toBeVisible();
    await expect(page.locator("#cancel-edit-btn")).toBeVisible();
    await expect(page.locator("#edit-artifact-btn")).toBeHidden();
    await expect(page.locator("#delete-version-btn")).toBeHidden();
  });

  test("Cancel exits edit mode", async ({ page }) => {
    await loadArtifactFixture(page);

    await page.locator("#edit-artifact-btn").click();
    await expect(page.locator("#artifact-editor")).toBeVisible();

    await page.locator("#cancel-edit-btn").click();

    await expect(page.locator("#artifact-editor")).toBeHidden();
    await expect(page.locator("#edit-artifact-btn")).toBeVisible();
  });

  test("Save creates new version and jumps to it", async ({ page }) => {
    await loadArtifactFixture(page);

    await page.locator("#edit-artifact-btn").click();

    const textarea = page.locator("#artifact-editor");
    await textarea.fill("(name Test)\n(value edited)");

    await page.locator("#save-artifact-btn").click();

    // Wait for rebuild + version refetch
    await page.waitForTimeout(500);

    // Should exit edit mode
    await expect(page.locator("#artifact-editor")).toBeHidden();

    // Version display should show v3/v3
    await expect(page.locator("#artifact-version-display")).toHaveText(
      "v3 / v3",
    );
  });

  test("Delete button hidden when only 1 version", async ({ page }) => {
    resetMockState();
    mockArtifacts["single.sxpb"] = "(name Single)";
    mockArtifactVersions["single.sxpb"] = [["(name Single)", 0]];

    await setupMockRoutes(page);
    await page.goto("/");
    await page.waitForTimeout(500);

    await page.locator(".artifact-item").first().click();
    await page.waitForTimeout(500);

    await expect(page.locator("#delete-version-btn")).toBeHidden();
  });

  test("Delete removes a version", async ({ page }) => {
    await loadArtifactFixture(page);

    const deleteBtn = page.locator("#delete-version-btn");
    await expect(deleteBtn).toBeVisible();

    page.on("dialog", (dialog) => dialog.accept());
    await deleteBtn.click();

    await page.waitForTimeout(500);

    // Should now show v1/v1
    await expect(page.locator("#artifact-version-display")).toHaveText(
      "v1 / v1",
    );
  });

  test("Images do not show edit/delete buttons", async ({ page }) => {
    resetMockState();
    mockArtifacts["image.png"] = "data:image/png;base64,AAAA";
    mockArtifactVersions["image.png"] = [["data:image/png;base64,AAAA", 0]];

    await setupMockRoutes(page);
    await page.goto("/");
    await page.waitForTimeout(500);

    await page.locator(".artifact-item").first().click();
    await page.waitForTimeout(500);

    await expect(page.locator("#edit-artifact-btn")).toBeHidden();
    await expect(page.locator("#delete-version-btn")).toBeHidden();
  });
});
