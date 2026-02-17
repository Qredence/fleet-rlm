export class MentionDebounceController {
  private requestToken = 0;
  private timer: NodeJS.Timeout | null = null;

  public constructor(private readonly delayMs: number) {}

  public schedule(task: (token: number) => void): number {
    this.requestToken += 1;
    const token = this.requestToken;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      task(token);
    }, this.delayMs);
    return token;
  }

  public clear(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.requestToken += 1;
  }

  public isCurrent(token: number): boolean {
    return token === this.requestToken;
  }
}
