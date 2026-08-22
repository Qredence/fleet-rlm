/** File and Artifact slash commands: /volume, /attach, /files, /file, /artifact(s). */

import { open } from "node:fs/promises";
import { basename } from "node:path";

import { saveArtifact, writeFileAtomic } from "../../cli-core.js";
import { formatBytes, shortId } from "../format.js";
import type { Message } from "../store.js";

import type { CommandSpec } from "./registry.js";
import { appendSystem, errorMessage } from "./shared.js";

export const volumeCommand: CommandSpec = {
  name: "volume",
  description: "Show the Workspace Volume file tree",
  usage: "/volume [root]",
  handler: async (args, ctx) => {
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /volume [root]");
      return;
    }
    const root = args[0] ?? ".";
    try {
      const tree = await ctx.client.listVolumeTree({ root });
      const rendered = formatVolumeTree([...(tree.directories ?? []), ...tree.paths]);
      appendSystem(
        ctx.store,
        `Workspace Volume${root === "." ? "" : ` (${root})`}\n\n${rendered}${tree.truncated ? "\n\n…tree truncated; narrow the root or use a deeper command." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list Workspace Volume: ${errorMessage(error)}`);
    }
  },
};

export const attachCommand: CommandSpec = {
  name: "attach",
  description: "Upload local files as Attachments pinned to the next Turn",
  usage: "/attach <path>… | clear | list",
  handler: async (args, ctx) => {
    if (args.length === 0 || args[0] === "list") {
      const pending = ctx.store.getState().pendingAttachments;
      if (pending.length === 0) {
        appendSystem(ctx.store, "No Attachments pinned for the next Turn.");
        return;
      }
      const lines = pending
        .map(
          (attachment, index) =>
            `  ${String(index + 1).padStart(2)}. ${attachment.filename}  ${formatBytes(attachment.bytes)}  ${shortId(attachment.id)}`,
        )
        .join("\n");
      appendSystem(ctx.store, `Pinned Attachments for the next Turn\n\n${lines}`);
      return;
    }
    if (args[0] === "clear") {
      ctx.store.dispatch({ type: "attachment/clear" });
      appendSystem(ctx.store, "Pending Attachments cleared.");
      return;
    }
    for (const arg of args) {
      try {
        const handle = await open(arg, "r");
        let bytes: Buffer;
        try {
          const info = await handle.stat();
          if (!info.isFile()) {
            appendSystem(ctx.store, `${arg} is not a regular file.`);
            continue;
          }
          bytes = await handle.readFile();
        } finally {
          await handle.close();
        }
        const ref = await ctx.client.uploadAttachment({
          name: basename(arg),
          bytes,
          contentType: contentTypeFor(arg),
        });
        ctx.store.dispatch({
          type: "attachment/pin",
          attachment: {
            id: ref.id,
            filename: ref.filename,
            bytes: ref.byte_size,
            contentType: ref.content_type ?? undefined,
          },
        });
        appendSystem(
          ctx.store,
          `Attached ${ref.filename} (${formatBytes(ref.byte_size)}) for the next Turn.`,
        );
      } catch (error) {
        appendSystem(ctx.store, `Failed to attach ${arg}: ${errorMessage(error)}`);
      }
    }
  },
};

export const filesCommand: CommandSpec = {
  name: "files",
  description: "List the Workspace files/ root via /api/files",
  usage: "/files [path]",
  handler: async (args, ctx) => {
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /files [path]");
      return;
    }
    try {
      const listing = await ctx.client.listWorkspaceFiles({ path: args[0] ?? "." });
      if (listing.entries.length === 0) {
        appendSystem(ctx.store, `No Workspace files/ entries under “${args[0] ?? "."}”.`);
        return;
      }
      const lines = listing.entries
        .map((entry) =>
          entry.kind === "directory"
            ? `  ${entry.path}/`
            : `  ${entry.path}  ${entry.byte_size == null ? "—" : formatBytes(entry.byte_size)}`,
        )
        .join("\n");
      appendSystem(
        ctx.store,
        `Workspace files${args[0] ? ` (${args[0]})` : ""}\n\n${lines}${listing.truncated ? "\n\n…listing truncated; use /files <path> to narrow." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to list Workspace files: ${errorMessage(error)}`);
    }
  },
};

export const fileCommand: CommandSpec = {
  name: "file",
  description: "Show or save one file from the Workspace files/ root",
  usage: "/file <path> [save <localPath>]",
  handler: async (args, ctx) => {
    const path = args[0];
    if (!path) {
      appendSystem(ctx.store, "Usage: /file <path> [save <localPath>]");
      return;
    }
    if (args[1] === "save") {
      const localPath = args[2];
      if (!localPath) {
        appendSystem(ctx.store, "Usage: /file <path> save <localPath>");
        return;
      }
      try {
        let content = "";
        let cursor: string | undefined;
        let pages = 0;
        do {
          const page = await ctx.client.readWorkspaceFile(path, cursor ? undefined : 8_000);
          content += page.content;
          cursor = page.next_cursor ?? undefined;
          pages += 1;
          if (pages > 1_000) throw new Error("Workspace file is too large to save");
        } while (cursor);
        await writeFileAtomic(localPath, Buffer.from(content, "utf8"));
        appendSystem(ctx.store, `Saved Workspace file to ${localPath}.`);
      } catch (error) {
        appendSystem(ctx.store, `Failed to save ${path}: ${errorMessage(error)}`);
      }
      return;
    }
    if (args.length > 1) {
      appendSystem(ctx.store, "Usage: /file <path> [save <localPath>]");
      return;
    }
    try {
      const page = await ctx.client.readWorkspaceFile(path, 8_000);
      const preview = page.content.slice(0, 8_000);
      const lines = preview
        .split("\n")
        .map((line) => `  ${line}`)
        .join("\n");
      appendSystem(
        ctx.store,
        `Workspace file ${path} (${formatBytes(page.byte_size)})\n\n${lines}${page.content.length >= 8_000 ? "\n\n…preview truncated; use /file <path> save <localPath> for the full file." : ""}`,
      );
    } catch (error) {
      appendSystem(ctx.store, `Failed to read ${path}: ${errorMessage(error)}`);
    }
  },
};

export const artifactCommand: CommandSpec = {
  name: "artifact",
  description: "Download and verify an Artifact to a local path",
  usage: "/artifact <artifactId> <localPath>",
  handler: async (args, ctx) => {
    const [artifactId, localPath] = args;
    if (!artifactId || !localPath || args.length !== 2) {
      appendSystem(ctx.store, "Usage: /artifact <artifactId> <localPath>");
      return;
    }
    try {
      await saveArtifact(ctx.client, artifactId, localPath);
      appendSystem(ctx.store, `Saved verified Artifact to ${localPath}.`);
    } catch (error) {
      appendSystem(ctx.store, `Failed to save Artifact: ${errorMessage(error)}`);
    }
  },
};

export const artifactsCommand: CommandSpec = {
  name: "artifacts",
  description: "List Artifacts committed in this conversation",
  usage: "/artifacts",
  handler: (_args, ctx) => {
    const artifacts = ctx.store
      .getState()
      .messages.filter(
        (message): message is Extract<Message, { kind: "artifact" }> => message.kind === "artifact",
      );
    if (artifacts.length === 0) {
      appendSystem(ctx.store, "No Artifacts in this conversation.");
      return;
    }
    const lines = artifacts
      .map(
        (artifact, index) =>
          `  ${String(index + 1).padStart(2)}. ${artifact.artifactId}  ${artifact.name}  ${formatBytes(artifact.bytes)}  (${artifact.artifactKind})`,
      )
      .join("\n");
    appendSystem(
      ctx.store,
      `Artifacts\n\n${lines}\n\nUse /artifact <id> <localPath> to download and verify.`,
    );
  },
};

export function formatVolumeTree(paths: readonly string[]): string {
  if (paths.length === 0) return "(empty)";
  const root = new Map<string, Map<string, unknown>>();
  for (const raw of paths) {
    const parts = raw.replace(/^\.\//, "").split("/").filter(Boolean);
    let node = root;
    for (const part of parts) {
      let child = node.get(part);
      if (!child) {
        child = new Map<string, unknown>();
        node.set(part, child);
      }
      node = child as Map<string, Map<string, unknown>>;
    }
  }
  const lines: string[] = [];
  const visit = (node: Map<string, unknown>, prefix: string): void => {
    const entries = [...node.entries()].sort(([a], [b]) => a.localeCompare(b));
    entries.forEach(([name, child], index) => {
      const last = index === entries.length - 1;
      lines.push(`${prefix}${last ? "└── " : "├── "}${name}`);
      if ((child as Map<string, unknown>).size > 0)
        visit(child as Map<string, unknown>, `${prefix}${last ? "    " : "│   "}`);
    });
  };
  visit(root, "");
  return lines.join("\n");
}

const TEXT_EXTENSIONS = new Set([
  "csv",
  "html",
  "js",
  "json",
  "log",
  "md",
  "py",
  "sh",
  "toml",
  "ts",
  "txt",
  "xml",
  "yaml",
  "yml",
]);

function contentTypeFor(path: string): string {
  const extension = basename(path).split(".").pop()?.toLowerCase();
  return extension && TEXT_EXTENSIONS.has(extension) ? "text/plain" : "application/octet-stream";
}
