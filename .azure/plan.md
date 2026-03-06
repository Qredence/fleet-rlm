# Azure Deployment Plan

> **Status:** Executing

Generated: 2026-03-06

---

## 1. Project Overview

**Goal:** Roll out multitenant Microsoft Entra auth for the Fleet RLM web experience, keep the frontend/backend runtime wired through verified bearer tokens, and prepare the repo for Azure-hosted execution without introducing a second auth contract.

**Path:** Add Components

---

## 2. Requirements

| Attribute | Value |
|-----------|-------|
| Classification | Development |
| Scale | Small |
| Budget | Balanced |
| **Subscription** | Pending user confirmation |
| **Location** | Pending user confirmation |

---

## 3. Components Detected

| Component | Type | Technology | Path |
|-----------|------|------------|------|
| Fleet RLM UI | Frontend | Vite + React + TypeScript + MSAL Browser | `src/frontend` |
| Fleet RLM API | API | FastAPI + DSPy + WebSocket runtime | `src/fleet_rlm` |

---

## 4. Recipe Selection

**Selected:** AZD

**Rationale:** The repo already has a strong app-level execution plan and needs an Azure preparation artifact that can later drive validation/deploy. AZD is the cleanest fit for an integrated frontend/API deployment path and for capturing Azure context explicitly.

---

## 5. Architecture

**Stack:** Containers

### Service Mapping

| Component | Azure Service | SKU |
|-----------|---------------|-----|
| Fleet RLM API + bundled UI | Azure Container Apps | Consumption or dedicated profile TBD |
| Microsoft Entra SPA app registration | Entra App Registration | Multitenant (`AzureADMultipleOrgs`) |
| Microsoft Entra API app registration | Entra App Registration | Multitenant (`AzureADMultipleOrgs`) |

### Supporting Services

| Service | Purpose |
|---------|---------|
| Log Analytics | Centralized container and runtime logs |
| Application Insights | Monitoring, tracing, and auth/runtime telemetry |
| Key Vault | Secrets and env-backed auth/runtime config |
| Managed Identity | Future service-to-service Azure access without client secrets |
| Neon Postgres | Tenant allowlist source of truth and tenant-isolated runtime persistence |

### Entra decisions locked for implementation

- Organizational accounts only (no personal Microsoft accounts)
- SPA callback path: `/login`
- SPA redirect URIs:
  - `http://localhost:5173/login`
  - `https://staging.qredence.ai/login`
  - `https://app.qredence.ai/login`
- Post-logout redirect URIs: same as the SPA redirect URIs above
- Delegated API scope: `api://<api-app-client-id>/access_as_user`
- Frontend default authority: `https://login.microsoftonline.com/organizations`
- Backend issuer template default: `https://login.microsoftonline.com/{tenantid}/v2.0`
- Tenant admission: allowlisted tenants only via the Neon `tenants` table

---

## 6. Execution Checklist

### Phase 1: Planning
- [x] Analyze workspace
- [x] Gather requirements
- [ ] Confirm subscription and location with user
- [x] Scan codebase
- [x] Select recipe
- [x] Plan architecture
- [x] **User approved this plan**

### Phase 2: Execution
- [x] Research components (load references, invoke skills)
- [ ] Generate infrastructure files
- [ ] Generate application configuration
- [ ] Generate Dockerfiles (if containerized)
- [ ] Update plan status to "Ready for Validation"

### Phase 3: Validation
- [ ] Invoke azure-validate skill
- [ ] All validation checks pass
- [ ] Update plan status to "Validated"
- [ ] Record validation proof below

### Phase 4: Deployment
- [ ] Invoke azure-deploy skill
- [ ] Deployment successful
- [ ] Update plan status to "Deployed"

---

## 7. Validation Proof

> **⛔ REQUIRED**: The azure-validate skill MUST populate this section before setting status to `Validated`. If this section is empty and status is `Validated`, the validation was bypassed improperly.

| Check | Command Run | Result | Timestamp |
|-------|-------------|--------|-----------|
| Pending | Pending | Pending | Pending |

**Validated by:** Pending
**Validation timestamp:** Pending

---

## 8. Files to Generate

| File | Purpose | Status |
|------|---------|--------|
| `.azure/plan.md` | Azure execution source of truth | ✅ |
| `azure.yaml` | AZD configuration | ⏳ |
| `infra/` | Azure infrastructure definitions | ⏳ |
| `src/fleet_rlm/ui/dist` deployment config | Container/runtime packaging follow-through | ⏳ |

---

## 9. Next Steps

> Current: Implementing repo-side auth/runtime cleanup and waiting on Azure context details.

1. Confirm Azure subscription and region.
2. Create the multitenant SPA and API Entra app registrations with the redirect URIs above and expose `access_as_user`.
3. Generate Azure deployment artifacts only after the runtime/auth contract is validated locally.
