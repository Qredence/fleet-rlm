import { Editor, ProcessTerminal, TUI, type Terminal } from "@earendil-works/pi-tui";

import type { FleetApiClient, FleetSession } from "../fleet-api-client.js";
import { FleetAutocompleteProvider } from "./autocomplete.js";
import { PiCommandPresenter } from "./command-presenter.js";
import { parseInput, type CommandContext } from "./commands.js";
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
  private resolveFinished!: () => void;
  private readonly finished = new Promise<void>((resolve) => {
    this.resolveFinished = resolve;
  });

  constructor(private readonly options: FleetTuiOptions) {
    setTerminalColorScheme("dark");
    this.terminal = options.terminal ?? new ProcessTerminal();
    this.ui = new TUI(this.terminal);
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
  }

  start(): Promise<void> {
    if (this.started) return this.finished;
    this.started = true;
    this.unsubscribe = this.store.subscribe(() => this.onStateChange());
    this.ui.addInputListener((data) => {
      if (fleetKeybindings.matches(data, "fleet.exit")) {
        void this.stop();
        return { consume: true };
      }
      if (fleetKeybindings.matches(data, "fleet.cancel")) {
        if (isBusy(this.store.getState().run)) {
          this.controller.cancel();
        } else {
          void this.stop();
        }
        return { consume: true };
      }
      if (fleetKeybindings.matches(data, "fleet.suspend")) {
        this.suspend();
        return { consume: true };
      }
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
      const pending = this.store.getState().pendingSkillSelections;
      this.controller.start(parsed.text, {
        skillSelections: pending.map((selection) => ({
          id: selection.id,
          expected_version: selection.expectedVersion,
        })),
        onStreamOpen: () =>
          this.store.dispatch({ type: "skill-selection/consume", selections: pending }),
        onPreStreamFailure: (draft) => this.editor.setText(draft),
      });
    };
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
      presenter: new PiCommandPresenter(this.ui, this.editor, this.store),
    };
  }

  private onStateChange(): void {
    const busy = isBusy(this.store.getState().run);
    this.editor.disableSubmit = busy;
    this.terminal.setProgress(busy);
    this.ui.requestRender();
  }
}
