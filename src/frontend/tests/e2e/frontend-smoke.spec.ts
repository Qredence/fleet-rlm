import { expect, test } from "@playwright/test";

test("loads the supported shell surfaces without route crashes", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("button", { name: "RLM Workspace", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Volumes", exact: true })).toBeVisible();

  await expect(page.getByRole("button", { name: "Skills", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Memory", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Analytics", exact: true })).toHaveCount(0);

  await expect(page.getByRole("heading", { name: "Unexpected Application Error!" })).toHaveCount(0);
  await expect(page.getByText("We hit a rendering issue on this route")).toHaveCount(0);
});

test("legacy product routes redirect to the supported surfaces", async ({ page }) => {
  await page.goto("/app/skills");
  await expect(page).toHaveURL(/\/app\/workspace$/);

  await page.goto("/app/memory");
  await expect(page).toHaveURL(/\/app\/workspace$/);

  await page.goto("/app/analytics");
  await expect(page).toHaveURL(/\/app\/workspace$/);

  await page.goto("/app/taxonomy");
  await expect(page).toHaveURL(/\/app\/volumes$/);

  await page.goto("/app/taxonomy/demo-skill");
  await expect(page).toHaveURL(/\/app\/volumes$/);
});

test("sign-in dialog supports keyboard dismissal and restores focus", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.removeItem("fleet-rlm:access-token");
  });

  await page.goto("/");

  const signInTrigger = page.getByRole("button", {
    name: "Sign In",
    exact: true,
  });
  await expect(signInTrigger).toBeVisible();

  await signInTrigger.click();
  await expect(
    page.getByRole("button", { name: "Continue with Microsoft", exact: true }),
  ).toBeVisible();

  await page.keyboard.press("Escape");

  await expect(
    page.getByRole("button", { name: "Continue with Microsoft", exact: true }),
  ).toHaveCount(0);
  await expect(signInTrigger).toBeFocused();
});

test("opens settings without runtime exception", async ({ page }) => {
  await page.goto("/settings");
  await page.waitForURL(/\/app\/settings/);

  await expect(page.getByText("Appearance", { exact: true })).toBeVisible();
  await expect(page.getByText("Telemetry", { exact: true })).toBeVisible();
  await expect(page.getByText("LiteLLM Integration", { exact: true })).toBeVisible();

  const telemetryRow = page.getByText("Anonymous telemetry", { exact: true });
  await telemetryRow.scrollIntoViewIfNeeded();
  await expect(telemetryRow).toBeVisible();

  await expect(page.getByRole("heading", { name: "Unexpected Application Error!" })).toHaveCount(0);
  await expect(page.getByText("We hit a rendering issue on this route")).toHaveCount(0);
});
