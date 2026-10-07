import { Editor, ProcessTerminal, type Terminal, TuiAltScreen } from "@earendil-works/pi-tui";

import type { FleetApiClient, FleetSession } from "../fleet-api-client.js";
import { FleetAutocompleteProvider } from "./autocomplete.js";
import { PiCommandPresenter } from "./command-presenter.js";
import { type CommandContext, parseInput } from "./commands.js";
import type { DraftState, DraftStore } from "./draft-store.js";
import { fleetKeybindings } from "./keybindings.js";
import { RunController } from "./runner.js";
import { FleetScreen } from "./screen.js";
import { ConversationStore, isBusy, type StoreEvent } from "./store.js";
import {
  editorTheme,
  initTheme,
  isLightRgb,
  onThemeChange,
  setDetectedTerminalColorScheme,
  setTerminalBackground,
  stopThemeMonitoring,
  theme,
} from "./theme.js";

/** Settings policy path that holds the active model id shown in the header. */
const MODEL_SETTINGS_PATH = "llm.root.model";

export type FleetTuiApplication = {
  start(): Promise<void>;
  stop(): Promise<void>;
};

export type FleetTuiOptions = {
  client: FleetApiClient;
  session: FleetSession;
  resumed: boolean;
  initialEvents: StoreEvent[];
  latestTraceId?: string | null;
  latestTraceRunId?: string | null;
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
  private readonly ui: TuiAltScreen;
  /** Theme initialization (persisted selection load) — color queries wait on it. */
  private readonly themeReady: Promise<void>;
  private readonly store = new ConversationStore();
  private readonly controller: RunController;
  private readonly editor: Editor;
  private readonly screen: FleetScreen;
  private unsubscribe?: () => void;
  private started = false;
  private stopping?: Promise<void>;
  private lastCtrlCAt = 0;
  private resolveFinished: () => void = () => undefined;
  private readonly finished: Promise<void>;

  constructor(private readonly options: FleetTuiOptions) {
    this.finished = new Promise((resolve) => {
      this.resolveFinished = resolve;
    });
    this.themeReady = initTheme(process.env.FLEET_TUI_THEME);
    this.terminal = options.terminal ?? new ProcessTerminal();
    // Alternate-screen viewport: the transcript is app-owned, so the wheel
    // scrolls it (and drag selects text for copy) instead of doing nothing.
    this.ui = new TuiAltScreen(this.terminal, undefined, undefined, {
      mouse: true,
      wheelScrollLines: 3,
      // Style transcript-search matches from the Fleet theme. Resolve per call
      // so a live theme switch restyles matches.
      searchMatchStyle: (text) => theme.searchMatch()(text),
      searchCurrentMatchStyle: (text) => theme.currentSearchMatch()(text),
    });
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
      latestTraceId: options.latestTraceId,
      latestTraceRunId: options.latestTraceRunId,
    });
    this.screen = new FleetScreen(this.store, this.editor, this.terminal, this.ui);
    this.ui.setLayoutRoot(this.screen);
    this.configureEditor();
    void this.restoreDraft();
  }

  start(): Promise<void> {
    if (this.started) return this.finished;
    this.started = true;
    this.unsubscribe = this.store.subscribe(() => this.onStateChange());
    // Load the model name for the header bar — best-effort, never blocks startup.
    void this.options.client
      .getSettings()
      .then((settings) => {
        const model = settings.fields.find((field) => field.path === MODEL_SETTINGS_PATH)?.value;
        this.store.dispatch({
          type: "settings/model",
          model: typeof model === "string" ? model : null,
        });
      })
      .catch(() => undefined);
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
      if (fleetKeybindings.matches(data, "fleet.toggleFold")) {
        this.lastCtrlCAt = 0;
        if (this.ui.hasOverlay()) return undefined;
        this.toggleLatestFold();
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
    onThemeChange(() => {
      this.ui.invalidate();
      this.ui.requestRender(true);
    });
    this.ui.setFocus(this.editor);
    this.ui.start();
    this.onStateChange();
    if (this.options.queryColorScheme !== false) {
      void this.themeReady.then(() =>
        this.ui.queryTerminalColors({ timeoutMs: 300 }).then((colors) => {
          let changed = false;
          if (colors.background) {
            setTerminalBackground(colors.background);
            changed = true;
          }
          // Cache the detected scheme even when an explicit theme is active;
          // switching back to "system" should restore this terminal palette.
          if (colors.background) {
            const scheme = isLightRgb(colors.background) ? "light" : "dark";
            if (setDetectedTerminalColorScheme(scheme)) changed = true;
          }
          if (changed) {
            this.ui.invalidate();
            this.ui.requestRender(true);
          }
        }),
      );
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
      stopThemeMonitoring();
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
            text: `Unknown command: /${parsed.name}. Type / to browse commands or use /help.`,
            ts: Date.now(),
          },
        });
        return;
      }
      this.submitText(parsed.text);
    };
  }

  /** Toggle the latest child card before other foldable Tool/code/output cards. */
  private toggleLatestFold(): void {
    const messages = this.store.getState().messages;
    const latestChild = [...messages]
      .reverse()
      .find((message) => message.kind === "child_progress");
    if (latestChild) {
      this.store.dispatch({ type: "message/toggle-fold", id: latestChild.id });
      return;
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (
        message &&
        (message.kind === "tool" || message.kind === "code" || message.kind === "output")
      ) {
        if (message.kind === "tool" && message.status === "running") return;
        this.store.dispatch({ type: "message/toggle-fold", id: message.id });
        return;
      }
    }
  }

  private submitText(text: string): void {
    const state = this.store.getState();
    if (state.session?.status === "archived") {
      this.editor.setText(text);
      this.store.dispatch({
        type: "message/upsert",
        message: {
          id: `archived-session-${Date.now()}`,
          kind: "text",
          role: "system",
          text: "This Session is archived and read-only. Open /sessions and press Ctrl+A on it to unarchive before sending a Turn.",
          ts: Date.now(),
          streaming: false,
        },
      });
      return;
    }
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
      notify: (message) => this.ui.flash(message),
      presenter: new PiCommandPresenter(
        this.ui,
        this.editor,
        this.store,
        this.options.client,
        (message) => this.ui.flash(message),
      ),
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
    const session = this.store.getState().session;
    const state = this.draftState();
    if (!store || !session || !state) return;
    store.schedule(session.id, state);
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
