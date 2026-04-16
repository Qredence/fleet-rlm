import type { RuntimeConnectivityTestResponse, RuntimeStatusResponse } from "@/lib/rlm-api";

export interface WorkspaceRuntimeGuard {
  blocked: boolean;
  showWarning: boolean;
  title: string;
  description: string;
  guidance: string[];
}

function pushUniqueGuidance(target: string[], value: string | null | undefined) {
  const normalized = value?.trim();
  if (!normalized || target.includes(normalized)) {
    return;
  }
  target.push(normalized);
}

function appendTestGuidance(
  guidance: string[],
  test: RuntimeConnectivityTestResponse | null | undefined,
) {
  if (!test || test.ok) {
    return;
  }

  pushUniqueGuidance(guidance, test.error);
  for (const item of test.guidance ?? []) {
    pushUniqueGuidance(guidance, item);
  }
}

function hasBlockingPreflightFailure(status: RuntimeStatusResponse): boolean {
  const llm = status.llm ?? {};
  const daytona = status.daytona ?? {};

  return llm.model_set === false || llm.api_key_set === false || daytona.configured === false;
}

function hasBlockingConnectivityFailure(status: RuntimeStatusResponse): boolean {
  return Boolean(
    (status.tests?.lm != null && !status.tests.lm.ok) ||
      (status.tests?.daytona != null && !status.tests.daytona.ok),
  );
}

export function getWorkspaceRuntimeGuard(
  status: RuntimeStatusResponse | null | undefined,
): WorkspaceRuntimeGuard {
  if (!status) {
    return {
      blocked: false,
      showWarning: false,
      title: "Runtime configuration required",
      description: "Fix runtime credentials or connectivity before starting a Workbench run.",
      guidance: [],
    };
  }

  const guidance: string[] = [];
  for (const item of status.guidance ?? []) {
    pushUniqueGuidance(guidance, item);
  }
  const daytonaGuidance =
    status.daytona != null &&
    "guidance" in status.daytona &&
    Array.isArray(status.daytona.guidance)
      ? status.daytona.guidance
      : [];
  for (const item of daytonaGuidance) {
    pushUniqueGuidance(guidance, item);
  }
  appendTestGuidance(guidance, status.tests?.lm);
  appendTestGuidance(guidance, status.tests?.daytona);

  const blocked = hasBlockingPreflightFailure(status) || hasBlockingConnectivityFailure(status);
  const showWarning = guidance.length > 0 && (blocked || status.ready === false);

  return {
    blocked,
    showWarning,
    title: blocked ? "Runtime configuration required" : "Runtime checks recommended",
    description: blocked
      ? "Fix runtime credentials or connectivity before starting a Workbench run."
      : "Run runtime checks before starting a Workbench run so failures surface before a long task starts.",
    guidance,
  };
}
