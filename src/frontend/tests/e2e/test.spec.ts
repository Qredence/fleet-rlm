import { expect, test } from "@playwright/test";

test("workspace chat container renders without errors", async ({ page }) => {
  await page.goto("/app/workspace");
  await page.waitForURL(/\/app\/workspace$/);

  // Core shell elements must be present
  await expect(page.getByRole("heading", { name: "Unexpected Application Error!" })).toHaveCount(0);
  await expect(page.getByText("We hit a rendering issue on this route")).toHaveCount(0);

  // Chat input area must be reachable
  const chatInput = page
    .locator('textarea, input[type="text"]')
    .filter({ hasText: "" })
    .or(page.locator('[placeholder*="Message"], [placeholder*="message"]'))
    .or(page.locator('[role="textbox"]'))
    .first();

  await expect(chatInput).toBeVisible({ timeout: 10_000 });

  // Filling the input should not cause an error
  await chatInput.fill(
    "Create a data processing task: generate a CSV, filter rows, run the script.",
  );
  await expect(chatInput).not.toBeEmpty();

  // Clear the input and verify the empty state returns
  await chatInput.clear();
  await expect(chatInput).toBeEmpty();
});
