export function clarificationIntro(phaseNum: 1 | 2): string {
  return phaseNum === 1
    ? "I have a few questions to refine the plan. This helps me generate a more targeted skill."
    : "Let me understand what changes you need. A couple of quick questions:";
}

export function phase1AssistantSummary(userTask: string): string {
  return `I've analyzed your request and identified the following:\n\n**Domain:** Development\n**Category:** Testing / Quality Assurance\n\n**Intent Analysis:**\n• **Purpose:** ${userTask}\n• **Problem:** Manual test writing is time-consuming and often incomplete\n• **Value:** Reduce testing overhead by 60% while improving coverage\n\n**Suggested Taxonomy Path:**\n\`/development/testing/test-generation\``;
}

export function phase1ClarificationSummary(answers: string[]): string {
  return `Thanks for clarifying! I've refined the plan based on your inputs:\n\n**Updated Scope:** ${answers[0]}\n**Language Support:** ${answers[1]}\n**Coverage Model:** ${answers[2]}\n\n**Revised Intent Analysis:**\n• **Purpose:** Automated test suite generation\n• **Problem:** Manual testing cannot keep up with CI/CD velocity\n• **Value:** Reduce testing overhead by 70% with targeted coverage`;
}

export function phase2AssistantSummary(): string {
  return "Content generation complete. I've created the full documentation and working demonstrations in the canvas.";
}

export function phase2ClarificationSummary(answers: string[]): string {
  return `I've updated the generated content based on your feedback:\n\n• **Changes applied:** ${answers[0]}\n• **Format updates:** ${answers[1]}\n\nThe canvas panel now reflects these changes.`;
}

export function phase3ValidationSummary(): string {
  return "**Validation Results:**\n• Compliance: Passed\n• Quality Score: 94/100\n• Edge Cases: 100% verified\n\nSkill successfully registered.";
}
