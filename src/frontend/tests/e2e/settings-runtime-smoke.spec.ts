import { expect, test } from "@playwright/test";

test("settings renders runtime health and connectivity controls", async ({ page }) => {
  await page.goto("/app/settings?section=runtime");
  await page.waitForURL(/\/app\/settings/);

  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByText("Runtime Status", { exact: true })).toBeVisible();
  await expect(page.getByText(/Runtime readiness is (healthy|degraded)\./)).toBeVisible();

  await expect(
    page.getByRole("heading", {
      name: "Test Credentials + Connection",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Test Daytona", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Test LM", exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Test All Connections", exact: true }),
  ).toBeVisible();

  await expect(page.getByText("Daytona Smoke", { exact: true })).toBeVisible();
  await expect(page.getByText("LM Smoke", { exact: true })).toBeVisible();
  await expect(page.getByText("Preflight Checks", { exact: true })).toBeVisible();
  await expect(page.getByText("Guidance", { exact: true })).toBeVisible();

  await expect(page.getByRole("heading", { name: "Unexpected Application Error!" })).toHaveCount(0);
  await expect(page.getByText("We hit a rendering issue on this route")).toHaveCount(0);
});
