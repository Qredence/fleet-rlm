import { test, expect } from "@playwright/test";

test("analyze chat container and canvas formatting", async ({ page }) => {
  test.setTimeout(180000); // 3 minutes for complex multi-step processing
  console.log("Navigating to /app/workspace ...");
  await page.goto("/app/workspace", { waitUntil: "networkidle" });
  await page.waitForTimeout(5000);

  // Type a complex message that triggers multiple events
  const inputSelector =
    'textarea, input[type="text"][placeholder*="Message"], [contenteditable="true"]';
  await page.fill(
    inputSelector,
    "Create a complex data processing task: 1. Generate a large random CSV file. 2. Write a Python script to filter rows based on a condition. 3. Run the script. 4. Show me each step in detail.",
  );
  await page.press(inputSelector, "Enter");

  console.log(
    "Message sent. Waiting 2 minutes for multi-step processing and event rendering...",
  );
  // We wait longer to ensure we see the sequence of events
  for (let i = 0; i < 6; i++) {
    await page.waitForTimeout(20000);
    console.log(
      `Waited ${20 * (i + 1)} seconds. Capturing intermediate screenshot...`,
    );
    await page.screenshot({
      path: `/tmp/phase_22_event_progress_${i}.png`,
      fullPage: true,
    });
  }

  // Final capture
  await page.screenshot({
    path: "/tmp/phase_22_final_formatting.png",
    fullPage: true,
  });

  const bodyText = await page.evaluate(() => document.body.innerText);
  console.log("--- FINAL FORMATTING ANALYSIS ---");
  console.log(`Length of UI text: ${bodyText.length}`);
});
