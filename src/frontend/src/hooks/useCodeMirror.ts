/**
 * useCodeMirror — React hook that mounts a CodeMirror 6 editor into a
 * container ref and keeps it in sync with external state.
 *
 * CodeMirror packages are loaded via a **barrel-module dynamic import**:
 * `codemirror-modules.ts` uses normal static imports (which Vite
 * pre-bundles), and this hook dynamically imports that single local
 * file.  Vite can always resolve a relative `.ts` path in a dynamic
 * `import()`, so the barrel + its pre-bundled CM deps become an async
 * chunk that loads cleanly in both the Figma Make sandbox and regular
 * Vite dev.
 *
 * The CodeMirror theme (`codemirror-theme.ts`) also uses direct imports
 * from the barrel, keeping all `@codemirror/*` references in files that
 * Vite's standard dependency pipeline processes.
 *
 * Features:
 *   - Python language support with syntax highlighting
 *   - Custom theme using the project's CSS design tokens
 *   - Line numbers, active line highlight, bracket matching
 *   - Controlled value via `onChange` callback
 *   - Theme-aware (re-renders match light/dark mode automatically via CSS vars)
 */
import { useRef, useEffect, useState } from "react";

interface UseCodeMirrorOptions {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
}

export function useCodeMirror({ value, onChange, readOnly = false }: UseCodeMirrorOptions) {
  const containerRef = useRef<HTMLDivElement>(null);
  // oxlint-disable-next-line @typescript-eslint/no-explicit-any
  const viewRef = useRef<any>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Whether the editor has been mounted
  const [editorReady, setEditorReady] = useState(false);

  // Track whether the current update is programmatic (from React state)
  const isExternalUpdate = useRef(false);

  // Keep the latest value accessible to the mount effect without re-running it
  const latestValue = useRef(value);
  latestValue.current = value;

  // Mount editor via barrel-module dynamic import
  useEffect(() => {
    if (!containerRef.current) return;

    let destroyed = false;
    // oxlint-disable-next-line @typescript-eslint/no-explicit-any
    let editorView: any = null;

    (async () => {
      try {
        // Dynamic import of the local barrel file — Vite resolves this
        // as a normal relative path and bundles it as an async chunk
        // containing all CodeMirror dependencies.
        const CM = await import("@/lib/data/codemirror-modules");
        const { createEditorTheme, createSyntaxHighlighting } =
          await import("@/lib/data/codemirror-theme");

        if (destroyed || !containerRef.current) return;

        // Build theme & syntax highlighting
        const editorTheme = createEditorTheme(CM);
        const syntaxHighlightingExt = createSyntaxHighlighting(CM);

        const extensions = [
          editorTheme,
          syntaxHighlightingExt,
          CM.python(),
          CM.lineNumbers(),
          CM.highlightActiveLine(),
          CM.bracketMatching(),
          CM.indentOnInput(),
          CM.history(),
          CM.keymap.of([
            ...CM.defaultKeymap,
            ...CM.historyKeymap,
            CM.indentWithTab,
          ] as unknown as Parameters<typeof CM.keymap.of>[0]),
          CM.EditorView.lineWrapping,
          CM.EditorView.updateListener.of(
            (update: { docChanged: boolean; state: { doc: { toString(): string } } }) => {
              if (update.docChanged && !isExternalUpdate.current) {
                const newValue = update.state.doc.toString();
                onChangeRef.current?.(newValue);
              }
            },
          ),
        ];

        if (readOnly) {
          extensions.push(CM.EditorState.readOnly.of(true));
        }

        const state = CM.EditorState.create({
          doc: latestValue.current,
          extensions,
        });

        if (destroyed || !containerRef.current) return;

        const view = new CM.EditorView({
          state,
          parent: containerRef.current,
        });

        editorView = view;
        viewRef.current = view;
        setEditorReady(true);
      } catch (err) {
        // CodeMirror failed to load — fall back to the textarea UI.
        console.warn(
          "[useCodeMirror] CodeMirror failed to initialise — falling back to textarea:",
          err,
        );
      }
    })();

    return () => {
      destroyed = true;
      if (editorView) {
        editorView.destroy();
      }
      viewRef.current = null;
      setEditorReady(false);
    };
  }, [readOnly]);

  // Sync value from React state → CodeMirror (when file changes externally)
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentDoc = view.state.doc.toString();
    if (currentDoc !== value) {
      isExternalUpdate.current = true;
      view.dispatch({
        changes: {
          from: 0,
          to: currentDoc.length,
          insert: value,
        },
      });
      isExternalUpdate.current = false;
    }
  }, [value]);

  return { containerRef, viewRef, editorReady };
}
