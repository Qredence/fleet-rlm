import { Link } from "react-router";
import { AlertTriangle } from "lucide-react";
import { Button } from "../components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "../components/ui/alert";
import { typo } from "../components/config/typo";
import {
  BACKEND_CAPABILITY_BANNER_TITLE,
  UNSUPPORTED_SECTION_REASON,
} from "../lib/rlm-api";

interface BackendCapabilityPageProps {
  sectionLabel: string;
  reason?: string;
}

export function BackendCapabilityPage({
  sectionLabel,
  reason = UNSUPPORTED_SECTION_REASON,
}: BackendCapabilityPageProps) {
  return (
    <div className="flex h-full w-full items-center justify-center px-6 py-10">
      <div className="w-full max-w-xl space-y-4">
        <Alert>
          <AlertTriangle className="text-muted-foreground" />
          <AlertTitle style={typo.label}>{BACKEND_CAPABILITY_BANNER_TITLE}</AlertTitle>
          <AlertDescription style={typo.caption}>
            {sectionLabel} is currently disabled. {reason}
          </AlertDescription>
        </Alert>
        <div className="flex items-center gap-3">
          <Button asChild variant="outline">
            <Link to="/">Back to Chat</Link>
          </Button>
          <Button asChild variant="ghost">
            <a href="/ready" target="_blank" rel="noreferrer">
              Check Backend Ready
            </a>
          </Button>
        </div>
      </div>
    </div>
  );
}
