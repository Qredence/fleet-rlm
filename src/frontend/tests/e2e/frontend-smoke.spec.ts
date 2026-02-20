import { expect, test } from "@playwright/test";

test("loads app shell without router crash", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("button", { name: "Chat", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Skills", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Taxonomy", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Memory", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Analytics", exact: true })).toBeVisible();

  await expect(
    page.getByRole("heading", { name: "Unexpected Application Error!" }),
  ).toHaveCount(0);
});

test("opens settings from user menu without runtime exception", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "User menu" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();

  await expect(page.getByText("Auto-save drafts")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Unexpected Application Error!" }),
  ).toHaveCount(0);
});

test("navigates primary tabs without hitting route error boundary", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(page.getByText("Skill Library")).toBeVisible();

  await page.getByRole("button", { name: "Taxonomy", exact: true }).click();
  await expect(page.getByText("Skill Taxonomy")).toBeVisible();

  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await expect(page.getByText("Memory")).toBeVisible();

  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await expect(page.getByText("Analytics")).toBeVisible();

  await expect(page.getByText("We hit a rendering issue on this route")).toHaveCount(0);
});
