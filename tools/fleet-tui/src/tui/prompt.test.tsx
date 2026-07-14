import { renderToString } from "ink";
import { describe, expect, it } from "vitest";

import { Prompt, PromptHelp } from "./prompt.js";

describe("prompt layout", () => {
  it("keeps keyboard help outside the editable prompt", () => {
    const prompt = renderToString(
      <Prompt busy={false} active onSubmit={() => undefined} onCancel={() => undefined} />,
    );
    const help = renderToString(<PromptHelp busy={false} active />, { columns: 160 });

    expect(prompt).toContain("type a prompt");
    expect(prompt).not.toContain("Enter to send");
    expect(help).toContain("Enter to send");
    expect(help).toContain("End: bottom");
  });
});
