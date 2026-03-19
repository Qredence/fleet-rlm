/**
 * Shared error formatting for settings mutation callbacks.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
