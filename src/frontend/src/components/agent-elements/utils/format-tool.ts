function arePartsEqual(prev: any, next: any): boolean {
  if (prev.toolCallId !== next.toolCallId) return false;
  if (prev.type !== next.type) return false;
  if (prev.state !== next.state) return false;

  if (prev.input !== next.input) {
    if (JSON.stringify(prev.input || {}) !== JSON.stringify(next.input || {})) return false;
  }
  if (prev.output !== next.output) {
    if (JSON.stringify(prev.output || {}) !== JSON.stringify(next.output || {})) return false;
  }

  return true;
}

function isToolCompleted(part: any): boolean {
  if (part.output !== undefined && part.output !== null) return true;
  if (part.state === "error") return true;
  if (part.state === "result") return true;
  return false;
}

/** Deep compare function for tool part props. Used with React.memo(). */
export function areToolPropsEqual(
  prevProps: { part: any; chatStatus?: string },
  nextProps: { part: any; chatStatus?: string },
): boolean {
  const partsEqual = arePartsEqual(prevProps.part, nextProps.part);
  if (!partsEqual) return false;
  if (isToolCompleted(nextProps.part)) return true;
  if (prevProps.chatStatus !== nextProps.chatStatus) return false;
  return true;
}

/** Get tool status from part state */
export function getToolStatus(part: any, chatStatus?: string) {
  const basePending = part.state !== "output-available" && part.state !== "output-error";
  const isError =
    part.state === "output-error" ||
    (part.state === "output-available" && part.output?.success === false);
  const isSuccess = part.state === "output-available" && !isError;
  const isPending = basePending && chatStatus === "streaming";
  const isInterrupted = basePending && chatStatus !== "streaming" && chatStatus !== undefined;

  return { isPending, isError, isSuccess, isInterrupted };
}
