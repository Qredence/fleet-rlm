## Contents

- Tier-based network restrictions
- Create sandboxes with network restrictions
- Update network settings while a sandbox is running
- Network allow list format
- Organization configuration
- Test network access
- Security benefits
- Essential services
- Troubleshooting
- See Also




Daytona provides network egress limiting for sandboxes to control internet access. This feature can be automatically applied based on your [organization's limits](../platform/limits.md) or manually configured for specific sandboxes.

## Tier-based network restrictions

Network limits are automatically applied to sandboxes based on your organization's billing tier. This provides secure and controlled internet access for development environments:

- **Tier 1 & Tier 2**: Network access is restricted and cannot be overridden at the sandbox level. Organization-level network restrictions take precedence over sandbox-level settings. Even with [`networkAllowList`](#create-sandboxes-with-network-restrictions) specified when creating a sandbox, the organization's network restrictions still apply
- **Tier 3 & Tier 4**: Full internet access is available by default, with the ability to configure custom network settings

> To learn more about organization tiers and limits, see [limits](../platform/limits.md).

[Essential services](#essential-services) are available on all tiers and include services essential for development: package registries, container registries, Git repositories, CDN services, platform services, and system package managers.

## Create sandboxes with network restrictions

Daytona provides methods to control network access when [creating sandboxes](./sandboxes.md#create-sandboxes) by using the `networkAllowList` and `networkBlockAll` parameters:

```go
package main

import (
	"context"
	"log"

	"github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
	"github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
	client, err := daytona.NewClient()
	if err != nil {
		log.Fatal(err)
	}
	ctx := context.Background()

	// Allow access to specific IP addresses (Wikipedia, X/Twitter, private network)
	allowList := "208.80.154.232/32,199.16.156.103/32,192.168.1.0/24"
	sandbox, err := client.Create(ctx, types.SnapshotParams{
		SandboxBaseParams: types.SandboxBaseParams{
			NetworkAllowList: &allowList,
		},
	})

	// Or block all network access
	sandbox, err = client.Create(ctx, types.SnapshotParams{
		SandboxBaseParams: types.SandboxBaseParams{
			NetworkBlockAll: true,
		},
	})
}
```
> **Note:**
> If both `networkBlockAll` and `networkAllowList` are specified, `networkBlockAll` takes precedence and all network access will be blocked, ignoring the allow list.

## Update network settings while a sandbox is running

Daytona provides methods to update network settings for running sandboxes. Organizations on [Tier 3 and Tier 4](#tier-based-network-restrictions) can change outbound firewall policy after the sandbox is created. The API applies the new rules on the runner and persists them on the sandbox record. The sandbox keeps running; stop or start are not required.

The request must include at least one of `networkBlockAll` or `networkAllowList`. Rules match create-time behavior and use the same [allow list format](#network-allow-list-format).

- Sending `networkAllowList` as an empty string clears a stored allow list
- Sending `networkBlockAll: true` blocks all outbound traffic and clears the allow list
- Sending only `networkBlockAll: false` restores general outbound access (for your tier) and clears a stored allow list

This operation requires the `WRITE_SANDBOXES` permission. Organizations on Tier 1 or Tier 2 cannot override network policy at the sandbox level, and the API returns an error in that case.

```go
import apiclient "github.com/daytonaio/daytona/libs/api-client-go"

settings := apiclient.NewUpdateSandboxNetworkSettings()
settings.SetNetworkBlockAll(true)
if err := sandbox.UpdateNetworkSettings(ctx, *settings); err != nil {
	log.Fatal(err)
}

restore := apiclient.NewUpdateSandboxNetworkSettings()
restore.SetNetworkBlockAll(false)
if err := sandbox.UpdateNetworkSettings(ctx, *restore); err != nil {
	log.Fatal(err)
}

allow := apiclient.NewUpdateSandboxNetworkSettings()
allow.SetNetworkAllowList("208.80.154.232/32,192.168.1.0/24")
if err := sandbox.UpdateNetworkSettings(ctx, *allow); err != nil {
	log.Fatal(err)
}
```

## Network allow list format

The network allow list is a comma-separated list of IPv4 CIDR blocks. Set your allowed networks using the `networkAllowList` parameter when [creating a sandbox](./sandboxes.md#create-sandboxes) or when [updating settings on a running sandbox](#update-network-settings-while-a-sandbox-is-running).

- **IPv4 only**: hostnames, domains, and IPv6 are not supported
- **CIDR required**: every entry must include a `/` prefix length integer in the range `0` to `32` (inclusive), for example: `/32`
- **CIDR format**: use standard CIDR notation (`A.B.C.D/N`). Do not include extra `/` segments
- **Max 10 entries**: the list cannot contain more than 10 comma-separated items
- **Whitespace is ignored**: entries are trimmed, so spaces around commas are ok

The following examples are valid:

- **Single IP**: `208.80.154.232/32` (Wikipedia)
- **Subnet**: `192.168.1.0/24` (Private network)
- **Multiple networks**: `208.80.154.232/32,199.16.156.103/32,10.0.0.0/8`

## Organization configuration

The network access policies for your organization are set automatically depending on your organization's limits tier and cannot be modified by organization administrators. These policies determine the default network behavior for all sandboxes in your organization.

## Test network access

To test network connectivity from your sandbox:

```bash
# Test HTTP connectivity to allowed addresses
curl -I https://208.80.154.232

# Test package manager access (allowed on all tiers)
apt update  # For Ubuntu/Debian
npm ping    # For Node.js
pip install --dry-run requests  # For Python
```

## Security benefits

Network limits provide several security advantages:

- **Prevents data exfiltration** from sandboxes
- **Reduces attack surface** by limiting external connections
- **Complies with security policies** for development environments
- **Enables fine-grained control** over network access
> **Caution:**
> Enabling unrestricted network access may pose security risks when executing untrusted code. It is recommended to whitelist specific network addresses using `networkAllowList` or block all network access using `networkBlockAll` instead.
>
> Test network connectivity before starting critical development work and consider upgrading your tier if you need access to many external services.

## Essential services

Daytona provides a list of essential services that are available on all tiers and can be used for development.
> **Note:**
> This list is continuously updated. If you require access to additional essential development services, submit a request in the [sandbox network whitelist](https://github.com/daytonaio/sandbox-network-whitelist) repository or contact [support@daytona.io](mailto:support@daytona.io).

### NPM registry and package managers

- **NPM Registry**: `registry.npmjs.org`, `registry.npmjs.com`, `nodejs.org`, `nodesource.com`, `npm.pkg.github.com`
- **Yarn Packages**: `yarnpkg.com`, `*.yarnpkg.com`, `yarn.npmjs.org`, `yarnpkg.netlify.com`
- **Bun**: `bun.sh`, `*.bun.sh`

### Git hosting and version control

- **GitHub**: `github.com`, `*.github.com`, `*.githubusercontent.com`, `ghcr.io`
- **GitLab**: `gitlab.com`, `*.gitlab.com`
- **Bitbucket**: `bitbucket.org`
- **Azure DevOps**: `dev.azure.com`, `*.dev.azure.com`, `login.microsoftonline.com`, `visualstudio.com`, `*.visualstudio.com`, `ssh.dev.azure.com`, `vs-ssh.visualstudio.com`

### Python package managers

- **PyPI**: `pypi.org`, `pypi.python.org`, `files.pythonhosted.org`, `bootstrap.pypa.io`, `astral.sh`

### Composer packages

- **Composer**: `*.packagist.org`, `packagist.com`

### Ubuntu/Debian package repositories

- **Ubuntu Repos**: `*.ubuntu.com`
- **Debian Repos**: `*.debian.org`, `cdn-fastly.deb.debian.org`

### CDN and content delivery

- **CDN Services**: `fastly.com`, `cloudflare.com`, `r2.cloudflarestorage.com`, `*.r2.cloudflarestorage.com`
- **JavaScript CDNs**: `unpkg.com`, `jsdelivr.net`

### AI/ML services

- **Anthropic**: `*.anthropic.com`, `claude.ai`, `platform.claude.com`
- **OpenAI**: `openai.com`, `*.openai.com`, `chatgpt.com`
- **Google AI**: `generativelanguage.googleapis.com`, `gemini.google.com`, `aistudio.google.com`, `ai.google.dev`, `models.dev`
- **Perplexity**: `api.perplexity.ai`
- **DeepSeek**: `api.deepseek.com`
- **Groq**: `api.groq.com`
- **Expo**: `api.expo.dev`
- **OpenRouter**: `openrouter.ai`
- **Qwen**: `chat.qwen.ai`, `dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com`
- **Cursor**: `*.cursor.com`
- **OpenCode**: `opencode.ai`, `*.opencode.ai`
- **Other AI Services**: `api.letta.com`, `api.fireworks.ai`, `open.bigmodel.cn`, `*.z.ai`, `*.moonshot.ai`, `ai-gateway.vercel.sh`, `api.featherless.ai`

### Docker registries and container services

- **Docker Registries**: `docker.io`, `*.docker.io`, `*.docker.com`
- **Microsoft Container Registry**: `mcr.microsoft.com`
- **Kubernetes Registry**: `registry.k8s.io`
- **Google Container Registry**: `gcr.io`, `*.gcr.io`, `registry.cloud.google.com`
- **Quay**: `quay.io`, `quay-registry.s3.amazonaws.com`

### Maven repositories

- **Maven Repos**: `repo1.maven.org`, `repo.maven.apache.org`

### Google Fonts

- **Google Fonts**: `fonts.googleapis.com`, `fonts.gstatic.com`

### AWS S3 endpoints

- **US East**: `s3.us-east-1.amazonaws.com`, `s3.us-east-2.amazonaws.com`
- **US West**: `s3.us-west-1.amazonaws.com`, `s3.us-west-2.amazonaws.com`
- **EU**: `s3.eu-central-1.amazonaws.com`, `s3.eu-west-1.amazonaws.com`, `s3.eu-west-2.amazonaws.com`

### Google Cloud Storage

- **GCS**: `storage.googleapis.com`

### Daytona

- **Daytona**: `app.daytona.io`

### Developer tools and services

- **Convex**: `convex.dev`, `*.convex.dev`, `*.convex.cloud`, `*.convex.site`
- **Heroku**: `herokuapp.com`, `*.herokuapp.com`
- **Vercel**: `vercel.com`, `*.vercel.com`, `*.vercel.app`
- **Supabase**: `supabase.com`, `*.supabase.com`, `supabase.co`, `*.supabase.co`
- **Clerk**: `clerk.com`, `*.clerk.com`, `clerk.dev`, `*.clerk.dev`, `accounts.dev`, `*.accounts.dev`, `clerk.accounts.dev`, `*.clerk.accounts.dev`
- **WorkOS**: `workos.com`, `*.workos.com`, `authkit.app`, `*.authkit.app`
- **Inngest**: `inngest.com`, `*.inngest.com`
- **PostHog**: `posthog.com`, `*.posthog.com`
- **Sentry**: `sentry.io`, `*.sentry.io`, `sentry-cdn.com`, `*.sentry-cdn.com`
- **Linear**: `linear.app`, `*.linear.app`
- **Figma**: `figma.com`, `*.figma.com`, `*.figmafiles.com`
- **ClickUp**: `clickup.com`, `*.clickup.com`
- **Playwright**: `playwright.dev`, `cdn.playwright.dev`

### Messaging services

- **Telegram**: `api.telegram.org`
- **WhatsApp**: `web.whatsapp.com`, `*.whatsapp.net`

### LLM observability

- **Langfuse**: `*.langfuse.com`, `*.cloud.langfuse.com`

## Troubleshooting

If you encounter network access issues or need unrestricted network access:

1. Verify your [organization tier](../platform/limits.md#tiers) in the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/limits)
2. Verify your [network allow list](#network-allow-list-format) configuration
3. Contact [support@daytona.io](mailto:support@daytona.io) for assistance

## See Also
- [Python SDK - network-limits](../python-sdk/network-limits.md)
- [TypeScript SDK - network-limits](../typescript-sdk/network-limits.md)
