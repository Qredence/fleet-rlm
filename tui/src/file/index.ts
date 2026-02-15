import { z } from "zod";
import { Ripgrep } from "./ripgrep";

export const InfoSchema = z.object({
  path: z.string(),
  name: z.string(),
  size: z.number(),
  mtime: z.number(),
  isDirectory: z.boolean(),
  isFile: z.boolean(),
  isSymlink: z.boolean(),
});

export const NodeSchema = z.object({
  path: z.string(),
  name: z.string(),
  type: z.enum(["file", "directory", "symlink"]),
  size: z.number().optional(),
  mtime: z.number().optional(),
});

export const ContentSchema = z.object({
  path: z.string(),
  content: z.string(),
  isBinary: z.boolean(),
  mimeType: z.string().optional(),
  encoding: z.enum(["utf8", "base64"]).optional(),
  truncated: z.boolean().optional(),
  lineCount: z.number().optional(),
  startLine: z.number().optional(),
});

export type Info = z.infer<typeof InfoSchema>;
export type Node = z.infer<typeof NodeSchema>;
export type Content = z.infer<typeof ContentSchema>;

const BINARY_EXTENSIONS = new Set([
  // Images
  "png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp", "svg", "ico",
  // Documents
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
  // Archives
  "zip", "tar", "gz", "bz2", "7z", "rar",
  // Executables
  "exe", "dll", "so", "dylib", "bin",
  // Media
  "mp3", "mp4", "avi", "mov", "wav", "flac",
  // Fonts
  "ttf", "otf", "woff", "woff2", "eot",
  // Other
  "db", "sqlite", "sqlite3", "o", "a", "class", "pyc", "pyd", "pyo",
]);

const IMAGE_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp", "svg", "ico",
]);

export class File {
  static async read(
    path: string,
    opts: {
      offset?: number;
      limit?: number;
      encoding?: "utf8" | "base64";
    } = {}
  ): Promise<Content> {
    const file = Bun.file(path);
    const exists = await file.exists();
    if (!exists) throw new Error(`File not found: ${path}`);

    const stats = await file.stat();
    if (stats.isDirectory()) {
      throw new Error(`Cannot read directory as file: ${path}`);
    }

    const isBinary = await this.isBinaryFile(path);
    const isImage = this.isImageFile(path);
    const mimeType = this.getMimeType(path);

    if (isImage && !opts.encoding) {
      const buffer = await file.arrayBuffer();
      const base64 = Buffer.from(buffer).toString("base64");
      return {
        path,
        content: base64,
        isBinary: true,
        mimeType,
        encoding: "base64",
      };
    }

    if (isBinary && !opts.encoding) {
      return {
        path,
        content: "[Binary file]",
        isBinary: true,
        mimeType,
      };
    }

    const text = await file.text();
    const lines = text.split("\n");
    const offset = opts.offset ?? 0;
    const limit = opts.limit ?? 2000;
    const sliced = lines.slice(offset, offset + limit);
    const isTruncated = sliced.length < lines.length;

    return {
      path,
      content: sliced.join("\n"),
      isBinary: false,
      mimeType,
      encoding: "utf8",
      truncated: isTruncated,
      lineCount: lines.length,
      startLine: offset,
    };
  }

  static async list(dir: string = ".", opts: { recursive?: boolean } = {}): Promise<Node[]> {
    const files = await Ripgrep.files(dir);
    const nodes: Node[] = [];

    for (const file of files) {
      const fullPath = dir === "." ? file : `${dir}/${file}`;
      const stats = await Bun.file(fullPath).stat().catch(() => null);

      if (stats) {
        nodes.push({
          path: fullPath,
          name: file.split("/").pop() || file,
          type: stats.isDirectory() ? "directory" : stats.isSymbolicLink() ? "symlink" : "file",
          size: stats.size,
          mtime: stats.mtime.getTime(),
        });
      }
    }

    return nodes;
  }

  static async search(
    query: string,
    dir: string = ".",
    opts: { limit?: number; dirs?: string[]; type?: "file" | "directory" } = {}
  ): Promise<Info[]> {
    const files = await Ripgrep.files(dir);
    const limit = opts.limit ?? 100;
    const results: Info[] = [];

    for (const file of files) {
      if (results.length >= limit) break;

      if (file.toLowerCase().includes(query.toLowerCase())) {
        const fullPath = dir === "." ? file : `${dir}/${file}`;
        const stats = await Bun.file(fullPath).stat().catch(() => null);

        if (stats) {
          const isDir = stats.isDirectory();
          if (opts.type && ((opts.type === "directory" && !isDir) || (opts.type === "file" && isDir))) {
            continue;
          }

          results.push({
            path: fullPath,
            name: file.split("/").pop() || file,
            size: stats.size,
            mtime: stats.mtime.getTime(),
            isDirectory: isDir,
            isFile: stats.isFile(),
            isSymlink: stats.isSymbolicLink(),
          });
        }
      }
    }

    return results;
  }

  static async status(dir: string = "."): Promise<{
    modified: string[];
    added: string[];
    deleted: string[];
    untracked: string[];
    renamed: string[];
  }> {
    const result = {
      modified: [] as string[],
      added: [] as string[],
      deleted: [] as string[],
      untracked: [] as string[],
      renamed: [] as string[],
    };

    try {
      const proc = Bun.spawn(["git", "status", "--porcelain", "-z"], {
        cwd: dir,
        stdout: "pipe",
        stderr: "pipe",
      });

      const exitCode = await proc.exited;
      if (exitCode !== 0) return result;

      const output = await new Response(proc.stdout).text();
      const entries = output.split("\0");

      for (const entry of entries) {
        if (entry.length < 3) continue;

        const status = entry.slice(0, 2);
        const path = entry.slice(3);

        switch (status.trim()) {
          case "M":
            result.modified.push(path);
            break;
          case "A":
            result.added.push(path);
            break;
          case "D":
            result.deleted.push(path);
            break;
          case "R":
            result.renamed.push(path);
            break;
          case "??":
            result.untracked.push(path);
            break;
        }
      }
    } catch {
      // Git not available or not a git repo
    }

    return result;
  }

  static async info(path: string): Promise<Info | null> {
    const stats = await Bun.file(path).stat().catch(() => null);
    if (!stats) return null;

    return {
      path,
      name: path.split("/").pop() || path,
      size: stats.size,
      mtime: stats.mtime.getTime(),
      isDirectory: stats.isDirectory(),
      isFile: stats.isFile(),
      isSymlink: stats.isSymbolicLink(),
    };
  }

  static async isBinaryFile(path: string): Promise<boolean> {
    const ext = path.split(".").pop()?.toLowerCase() || "";
    if (BINARY_EXTENSIONS.has(ext)) return true;

    const file = Bun.file(path);
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer.slice(0, 1024));

    for (const byte of bytes) {
      if (byte === 0) return true;
    }

    return false;
  }

  static isImageFile(path: string): boolean {
    const ext = path.split(".").pop()?.toLowerCase() || "";
    return IMAGE_EXTENSIONS.has(ext);
  }

  static getMimeType(path: string): string {
    const ext = path.split(".").pop()?.toLowerCase() || "";
    const mimeTypes: Record<string, string> = {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      bmp: "image/bmp",
      tiff: "image/tiff",
      webp: "image/webp",
      svg: "image/svg+xml",
      ico: "image/x-icon",
      pdf: "application/pdf",
      js: "application/javascript",
      ts: "application/typescript",
      json: "application/json",
      html: "text/html",
      css: "text/css",
      txt: "text/plain",
      md: "text/markdown",
      xml: "application/xml",
      zip: "application/zip",
      tar: "application/x-tar",
      gz: "application/gzip",
      mp3: "audio/mpeg",
      mp4: "video/mp4",
      wav: "audio/wav",
    };
    return mimeTypes[ext] || "application/octet-stream";
  }

  static async exists(path: string): Promise<boolean> {
    return await Bun.file(path).exists();
  }

  static async isDirectory(path: string): Promise<boolean> {
    const stats = await Bun.file(path).stat().catch(() => null);
    return stats?.isDirectory() ?? false;
  }
}
