import { expect, test } from "@playwright/test";

test("loads app shell without router crash", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("button", { name: "Chat", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Skills", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Volumes", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Memory", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Analytics", exact: true }),
  ).toBeVisible();

  await expect(
    page.getByRole("heading", { name: "Unexpected Application Error!" }),
  ).toHaveCount(0);
});

test("opens settings without runtime exception", async ({ page }) => {
  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

  await expect(page.getByText("Appearance", { exact: true })).toBeVisible();
  await expect(page.getByText("Telemetry", { exact: true })).toBeVisible();
  await expect(
    page.getByText("LiteLLM Integration", { exact: true }),
  ).toBeVisible();

  const telemetryRow = page.getByText("Anonymous telemetry", { exact: true });
  await telemetryRow.scrollIntoViewIfNeeded();
  await expect(telemetryRow).toBeVisible();

  await expect(
    page.getByRole("heading", { name: "Unexpected Application Error!" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("We hit a rendering issue on this route"),
  ).toHaveCount(0);
});

test("navigates primary tabs without hitting route error boundary", async ({
  page,
}) => {
  await page.goto("/");

  const tabExpectations = [
    { label: "Skills", content: "Skill Library" },
    { label: "Volumes", content: "Volume Browser" },
    { label: "Memory", content: "Memory" },
    { label: "Analytics", content: "Analytics" },
  ];

  for (const tabInfo of tabExpectations) {
    const tab = page.getByRole("button", { name: tabInfo.label, exact: true });
    await expect(tab).toBeVisible();

    const isDisabled = (await tab.getAttribute("aria-disabled")) === "true";
    if (isDisabled) {
      continue;
    }

    await tab.click();
    await expect(page.getByText(tabInfo.content)).toBeVisible();
  }

  await expect(
    page.getByText("We hit a rendering issue on this route"),
  ).toHaveCount(0);
});
