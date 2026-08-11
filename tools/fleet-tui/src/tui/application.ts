import {
  Editor,
  ProcessTerminal,
  TuiMainScreen,
  type TUI,
  type Terminal,
} from "@earendil-works/pi-tui";

import type { FleetApiClient, FleetSession } from "../fleet-api-client.js";
import { FleetAutocompleteProvider } from "./autocomplete.js";
import { PiCommandPresenter } from "./command-presenter.js";
import { parseInput, type CommandContext } from "./commands.js";
import type { DraftStore, DraftState } from "./draft-store.js";
import { RunController } from "./runner.js";
import { FleetScreen, isBusy } from "./screen.js";
import { ConversationStore, type StoreEvent } from "./store.js";
import { editorTheme, setTerminalColorScheme } from "./theme.js";
import { fleetKeybindings } from "./keybindings.js";

export type FleetTuiApplication = {
  start(): Promise<void>;
  stop(): Promise<void>;
};

export type FleetTuiOptions = {
  client: FleetApiClient;
  session: FleetSession;
  resumed: boolean;
  initialEvents: StoreEvent[];
  terminal?: Terminal;
  queryColorScheme?: boolean;
  /** Optional local draft persistence; omitted disables it (tests stay hermetic). */
  draftStore?: DraftStore;
};

export function createFleetTui(options: FleetTuiOptions): FleetTuiApplication {
  return new FleetTuiApplicationImpl(options);
}

class FleetTuiApplicationImpl implements FleetTuiApplication {
  private readonly terminal: Terminal;
  private readonly ui: TUI;
  private readonly store = new ConversationStore();
  private readonly controller: RunController;
  private readonly editor: Editor;
  private readonly screen: FleetScreen;
  private unsubscribe?: () => void;
  private started = false;
  private stopping?: Promise<void>;
  private lastCtrlCAt = 0;
  private resolveFinished!: () => void;
  private readonly finished = new Promise<void>((resolve) => {
    this.resolveFinished = resolve;
  });

  constructor(private readonly options: FleetTuiOptions) {
    setTerminalColorScheme("dark");
    this.terminal = options.terminal ?? new ProcessTerminal();
    this.ui = new TuiMainScreen(this.terminal);
    this.controller = new RunController(this.store, options.client);
    this.editor = new Editor(this.ui, editorTheme, { paddingX: 1, autocompleteMaxVisible: 8 });
    this.editor.setAutocompleteProvider(new FleetAutocompleteProvider(options.client));
    this.store.dispatch({
      type: "session/hydrate",
      session: {
        id: options.session.id,
        title: options.session.title,
        status: options.session.status,
        resumed: options.resumed,
      },
      events: options.initialEvents,
    });
    this.screen = new FleetScreen(this.store, this.editor, this.terminal, this.ui);
    this.ui.addChild(this.screen);
    this.configureEditor();
    void this.restoreDraft();
  }

  start(): Promise<void> {
    if (this.started) return this.finished;
    this.started = true;
    this.unsubscribe = this.store.subscribe(() => this.onStateChange());
    this.ui.addInputListener((data) => {
      if (fleetKeybindings.matches(data, "fleet.suspend")) {
        this.lastCtrlCAt = 0;
        this.suspend();
        return { consume: true };
      }
      if (fleetKeybindings.matches(data, "fleet.interrupt")) {
        this.lastCtrlCAt = 0;
        if (this.ui.hasOverlay() || this.editor.isShowingAutocomplete()) return undefined;
        if (isBusy(this.store.getState().run)) {
          this.controller.cancel();
          return { consume: true };
        }
        return undefined;
      }
      if (fleetKeybindings.matches(data, "fleet.clearOrExit")) {
        if (this.ui.hasOverlay()) return undefined;
        const now = Date.now();
        if (this.editor.getText()) {
          this.editor.setText("");
          this.lastCtrlCAt = now;
          return { consume: true };
        }
        if (now - this.lastCtrlCAt <= 750) void this.stop();
        this.lastCtrlCAt = now;
        return { consume: true };
      }
      if (fleetKeybindings.matches(data, "fleet.exit")) {
        this.lastCtrlCAt = 0;
        if (this.ui.hasOverlay() || this.editor.getText()) return undefined;
        void this.stop();
        return { consume: true };
      }
      this.lastCtrlCAt = 0;
      return undefined;
    });
    this.ui.setFocus(this.editor);
    this.ui.start();
    this.onStateChange();
    if (this.options.queryColorScheme !== false) {
      void this.ui.queryTerminalColorScheme({ timeoutMs: 150 }).then((scheme) => {
        if (!scheme) return;
        if (!setTerminalColorScheme(scheme)) return;
        this.ui.invalidate();
        this.ui.requestRender(true);
      });
    }
    return this.finished;
  }

  stop(): Promise<void> {
    if (this.stopping) return this.stopping;
    this.stopping = (async () => {
      if (isBusy(this.store.getState().run)) {
        await this.controller.cancelAndWait(1_000);
      }
      await this.persistDraft();
      this.unsubscribe?.();
      this.screen.dispose();
      this.terminal.setProgress(false);
      this.ui.stop();
      await this.terminal.drainInput(250, 25).catch(() => undefined);
      this.resolveFinished();
    })();
    return this.stopping;
  }

  private configureEditor(): void {
    this.editor.onSubmit = (text) => {
      const parsed = parseInput(text);
      if (parsed.kind === "empty") return;
      this.editor.addToHistory(text);
      if (parsed.kind === "command") {
        void parsed.spec.handler(parsed.args, this.commandContext());
        return;
      }
      if (parsed.kind === "unknown-command") {
        this.store.dispatch({
          type: "message/upsert",
          message: {
            id: `command-error-${Date.now()}`,
            kind: "error",
            text: `Unknown command: /${parsed.name}`,
            ts: Date.now(),
          },
        });
        return;
      }
      this.submitText(parsed.text);
    };
  }

  private submitText(text: string): void {
    const state = this.store.getState();
    const pending = state.pendingSkillSelections;
    const pendingAttachments = state.pendingAttachments;
    this.controller.start(text, {
      attachmentIds: pendingAttachments.map((attachment) => attachment.id),
      skillSelections: pending.map((selection) => ({
        id: selection.id,
        expected_version: selection.expectedVersion,
      })),
      onStreamOpen: () => {
        this.store.dispatch({ type: "skill-selection/consume", selections: pending });
        this.store.dispatch({ type: "attachment/consume", attachments: pendingAttachments });
      },
      onPreStreamFailure: (draft) => this.editor.setText(draft),
    });
  }

  private suspend(): void {
    if (this.options.terminal) return;
    this.ui.stop();
    process.once("SIGCONT", () => {
      this.ui.start();
      this.ui.setFocus(this.editor);
      this.ui.requestRender(true);
    });
    process.kill(process.pid, "SIGTSTP");
  }

  private commandContext(): CommandContext {
    return {
      store: this.store,
      client: this.options.client,
      cancelActiveRun: () => this.controller.cancelAndWait(1_000),
      exit: () => {
        void this.stop();
      },
      submit: (text) => this.submitText(text),
      presenter: new PiCommandPresenter(this.ui, this.editor, this.store),
    };
  }

  private onStateChange(): void {
    const busy = isBusy(this.store.getState().run);
    this.editor.disableSubmit = busy;
    this.terminal.setProgress(busy);
    this.persistDraftDebounced();
    this.ui.requestRender();
  }

  private draftState(): DraftState | null {
    const session = this.store.getState().session;
    if (!session) return null;
    const state = this.store.getState();
    return {
      draft: this.editor.getText(),
      pendingSkills: state.pendingSkillSelections,
      pendingAttachments: state.pendingAttachments,
      lastPrompt: state.lastPrompt,
    };
  }

  private persistDraftDebounced(): void {
    const store = this.options.draftStore;
    const state = this.draftState();
    if (!store || !state) return;
    store.schedule(state ? this.store.getState().session!.id : "", state);
  }

  private async persistDraft(): Promise<void> {
    const store = this.options.draftStore;
    const state = this.draftState();
    if (!store || !state) return;
    await store.flush();
  }

  private async restoreDraft(): Promise<void> {
    const store = this.options.draftStore;
    const session = this.store.getState().session;
    if (!store || !session) return;
    const restored = await store.load(session.id);
    if (!restored) return;
    if (restored.draft) this.editor.setText(restored.draft);
    if (restored.pendingSkills.length > 0) {
      this.store.dispatch({ type: "skill-selection/replace", selections: restored.pendingSkills });
    }
    if (restored.pendingAttachments.length > 0) {
      this.store.dispatch({
        type: "attachment/replace",
        attachments: restored.pendingAttachments,
      });
    }
    if (restored.lastPrompt && !this.store.getState().lastPrompt) {
      this.store.dispatch({ type: "user/prompt-restore", text: restored.lastPrompt });
    }
  }
}
