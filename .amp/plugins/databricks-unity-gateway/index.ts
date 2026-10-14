import type { PluginAPI, PluginToolContext } from "@ampcode/plugin"

export const description =
  "Adds guarded UG CLI tools and a Unity Gateway skill for Databricks model-service workflows."

const supportedAgents = ["claude", "codex", "gemini", "opencode", "copilot"] as const
type SupportedAgent = (typeof supportedAgents)[number]

function commandOutput(result: { exitCode: number; stdout: string; stderr: string }): string {
  const output = [result.stdout.trim(), result.stderr.trim()].filter(Boolean).join("\n")
  const sanitized = output
    .replace(/\bBearer\s+[^\s"']+/gi, "Bearer [REDACTED]")
    .replace(/\bdapi[a-zA-Z0-9_-]+\b/g, "[REDACTED DATABRICKS TOKEN]")
    .replace(/([?&]token=)[^\s&]+/gi, "$1[REDACTED]")

  if (result.exitCode !== 0) {
    throw new Error(`UG exited with status ${result.exitCode}: ${sanitized || "no output"}`)
  }
  return sanitized || "Command completed without output."
}

function requireString(input: Record<string, unknown>, key: string): string {
  const value = input[key]
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${key} must be a non-empty string`)
  }
  return value.trim()
}

function workspaceOrigin(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error("workspace_url must be an HTTPS Databricks workspace URL")
  }
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("workspace_url must be an HTTPS workspace origin without a path, credentials, or query")
  }
  return url.origin
}

export default async function register(amp: PluginAPI): Promise<void> {
  await amp.registerSkill({ path: "skills/using-databricks-unity-gateway" })

  amp.registerTool({
    name: "ug_status",
    title: "Check Unity Gateway CLI status",
    transcriptGroup: { active: "Checking UG status", complete: "Checked UG status" },
    description: "Read the local Unity Gateway CLI and coding-agent configuration status. Makes no changes.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    async execute(_input: Record<string, unknown>, _ctx: PluginToolContext) {
      return commandOutput(await amp.$`ug status --verbose low`)
    },
  })

  amp.registerTool({
    name: "ug_skills_list",
    title: "List Unity Gateway skills",
    transcriptGroup: { active: "Listing UG skills", complete: "Listed UG skills" },
    description: "List Databricks Unity Gateway skills configured for supported external coding agents. Makes no changes.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    async execute(_input: Record<string, unknown>, _ctx: PluginToolContext) {
      return commandOutput(await amp.$`ug skills list`)
    },
  })

  amp.registerTool({
    name: "ug_configure_agent",
    title: "Configure a supported coding agent",
    transcriptGroup: { active: "Configuring Databricks access", complete: "Configured Databricks access" },
    description:
      "Run UG configuration for one explicitly selected supported coding agent and Databricks workspace. Requires human confirmation; may open the OAuth sign-in flow and updates that agent's local configuration.",
    inputSchema: {
      type: "object",
      properties: {
        agent: {
          type: "string",
          enum: supportedAgents,
          description: "Which supported external coding agent to configure.",
        },
        workspace_url: {
          type: "string",
          description: "HTTPS workspace origin, for example https://my-company.cloud.databricks.com.",
        },
      },
      required: ["agent", "workspace_url"],
      additionalProperties: false,
    },
    async execute(input: Record<string, unknown>, ctx: PluginToolContext) {
      const agent = requireString(input, "agent")
      if (!supportedAgents.includes(agent as SupportedAgent)) {
        throw new Error(`agent must be one of: ${supportedAgents.join(", ")}`)
      }
      const workspace = workspaceOrigin(requireString(input, "workspace_url"))
      const confirmed = await ctx.ui.confirm({
        title: "Configure Databricks for this coding agent?",
        message: `UG will update the local ${agent} configuration for ${workspace}. This may start an OAuth sign-in flow. Continue?`,
        confirmButtonText: "Run UG configure",
        requireHuman: true,
      })
      if (!confirmed) return "Cancelled; no coding-agent configuration was changed."

      return commandOutput(
        await amp.$`ug configure --agents ${agent} --workspace ${workspace} --verbose low`,
      )
    },
  })
}
