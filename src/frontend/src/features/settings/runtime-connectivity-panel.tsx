import { AlertCircleIcon, BadgeCheckIcon, Clock3Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import { Field, FieldContent, FieldDescription, FieldTitle } from "@/components/ui/field";
import { FieldGroup } from "@/components/ui/field";
import type { RuntimeConnectivityTestResponse } from "@/lib/rlm-api";
import { formatCheckLabel } from "./runtime-status-panel";

function testSummary(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "Not run yet";
  if (test.ok) return "Pass";
  if (!test.preflight_ok) return "Preflight failed";
  return "Failed";
}

function testVariant(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "outline" as const;
  return test.ok ? ("default" as const) : ("destructive" as const);
}

function testLabel(test: RuntimeConnectivityTestResponse | null | undefined) {
  if (!test) return "Not run";
  return testSummary(test);
}

function formatCheckedAt(checkedAt: string | null | undefined) {
  if (!checkedAt) return null;
  return new Date(checkedAt).toLocaleString();
}

const CONN_FIELD_CLASSNAME = "border-b border-border-subtle py-4 last:border-b-0";

interface RuntimeConnectivityPanelProps {
  hasUnsavedRuntimeChanges: boolean;
  writeEnabled: boolean;
  daytonaTest: RuntimeConnectivityTestResponse | null | undefined;
  lmTest: RuntimeConnectivityTestResponse | null | undefined;
  llmChecks: [string, boolean][];
  daytonaChecks: [string, boolean][];
  runtimeGuidance: string[];
  onTestLm: () => void;
  onTestDaytona: () => void;
  onTestAll: () => void;
  testLmPending: boolean;
  testDaytonaPending: boolean;
}

export function RuntimeConnectivityPanel({
  hasUnsavedRuntimeChanges,
  daytonaTest,
  lmTest,
  llmChecks,
  daytonaChecks,
  runtimeGuidance,
  onTestLm,
  onTestDaytona,
  onTestAll,
  testLmPending,
  testDaytonaPending,
}: RuntimeConnectivityPanelProps) {
  return (
    <>
      <SectionCard variant="subtle" className="mt-4">
        <SectionCardHeader className="border-b border-border-subtle/70">
          <SectionCardTitle>Test Credentials + Connection</SectionCardTitle>
          <SectionCardDescription className="max-w-xl">
            Runs preflight credential checks plus live Daytona and LM connectivity smoke tests.
          </SectionCardDescription>
        </SectionCardHeader>
        <SectionCardContent className="pt-6">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2.5">
              <Button
                variant="outline"
                size="lg"
                className="rounded-lg"
                onClick={onTestLm}
                disabled={testLmPending}
              >
                {testLmPending ? "Testing LM…" : "Test LM"}
              </Button>
              <Button
                variant="outline"
                size="lg"
                className="rounded-lg"
                onClick={onTestDaytona}
                disabled={testDaytonaPending}
              >
                {testDaytonaPending ? "Testing Daytona…" : "Test Daytona"}
              </Button>
              <Button
                variant="secondary"
                size="lg"
                className="rounded-lg"
                onClick={onTestAll}
                disabled={testLmPending || testDaytonaPending}
              >
                Test All Connections
              </Button>
            </div>
            {hasUnsavedRuntimeChanges ? (
              <p className="text-xs leading-5 text-muted-foreground">
                Save runtime settings first so tests run against your latest credentials and
                provider configuration.
              </p>
            ) : null}
          </div>
        </SectionCardContent>
      </SectionCard>

      <FieldGroup className="gap-0">
        <Field orientation="responsive" className={CONN_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Daytona Smoke</FieldTitle>
            <FieldDescription>{`Last result: ${testSummary(daytonaTest)}`}</FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <Badge variant={testVariant(daytonaTest)}>
              {daytonaTest?.checked_at ? (
                daytonaTest.ok ? (
                  <BadgeCheckIcon />
                ) : (
                  <AlertCircleIcon />
                )
              ) : (
                <Clock3Icon />
              )}
              {testLabel(daytonaTest)}
            </Badge>
            {daytonaTest?.checked_at ? (
              <span className="text-xs text-muted-foreground">
                {formatCheckedAt(daytonaTest.checked_at)}
              </span>
            ) : null}
          </div>
        </Field>

        <Field orientation="responsive" className={CONN_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>LM Smoke</FieldTitle>
            <FieldDescription>{`Last result: ${testSummary(lmTest)}`}</FieldDescription>
          </FieldContent>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right">
            <Badge variant={testVariant(lmTest)}>
              {lmTest?.checked_at ? (
                lmTest.ok ? (
                  <BadgeCheckIcon />
                ) : (
                  <AlertCircleIcon />
                )
              ) : (
                <Clock3Icon />
              )}
              {testLabel(lmTest)}
            </Badge>
            {lmTest?.checked_at ? (
              <span className="text-xs text-muted-foreground">
                {formatCheckedAt(lmTest.checked_at)}
              </span>
            ) : null}
          </div>
        </Field>

        <Field orientation="responsive" className={CONN_FIELD_CLASSNAME}>
          <FieldContent>
            <FieldTitle>Preflight Checks</FieldTitle>
            <FieldDescription>Credential and provider availability.</FieldDescription>
          </FieldContent>
          <div className="flex max-w-xl flex-wrap justify-end gap-2">
            {llmChecks.map(([key, ok]) => (
              <Badge
                key={`llm-${key}`}
                variant={ok ? "outline" : "destructive"}
                className={ok ? "border-chart-3/30 bg-chart-3/10 text-chart-3" : undefined}
              >
                {ok ? <BadgeCheckIcon /> : <AlertCircleIcon />}
                LM {formatCheckLabel(key)}
              </Badge>
            ))}
            {daytonaChecks.map(([key, ok]) => (
              <Badge
                key={`daytona-${key}`}
                variant={ok ? "outline" : "destructive"}
                className={ok ? "border-chart-3/30 bg-chart-3/10 text-chart-3" : undefined}
              >
                {ok ? <BadgeCheckIcon /> : <AlertCircleIcon />}
                Daytona {formatCheckLabel(key)}
              </Badge>
            ))}
          </div>
        </Field>

        <Field orientation="responsive" className="py-4">
          <FieldContent>
            <FieldTitle>Guidance</FieldTitle>
            <FieldDescription>Actionable runtime recommendations.</FieldDescription>
          </FieldContent>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-right text-xs text-muted-foreground">
            {runtimeGuidance.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Field>
      </FieldGroup>
    </>
  );
}
