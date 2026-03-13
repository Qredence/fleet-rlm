import type { FileUIPart } from "ai";
import { nanoid } from "nanoid";

export type PromptInputErrorCode = "max_files" | "max_file_size" | "accept";

export type PromptInputError = {
  code: PromptInputErrorCode;
  message: string;
};

type PromptInputErrorHandler = (error: PromptInputError) => void;

export const convertBlobUrlToDataUrl = async (
  url: string,
): Promise<string | null> => {
  try {
    const response = await fetch(url);
    const blob = await response.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result as string);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
};

export const matchesAccept = (file: File, accept?: string): boolean => {
  if (!accept || accept.trim() === "") {
    return true;
  }

  const patterns = accept
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

  return patterns.some((pattern) => {
    if (pattern.endsWith("/*")) {
      return file.type.startsWith(pattern.slice(0, -1));
    }
    return file.type === pattern;
  });
};

export const filterAcceptedFiles = (
  files: File[],
  accept: string | undefined,
  onError?: PromptInputErrorHandler,
): File[] => {
  const accepted = files.filter((file) => matchesAccept(file, accept));
  if (files.length > 0 && accepted.length === 0) {
    onError?.({
      code: "accept",
      message: "No files match the accepted types.",
    });
  }
  return accepted;
};

export const filterFilesBySize = (
  files: File[],
  maxFileSize: number | undefined,
  onError?: PromptInputErrorHandler,
): File[] => {
  const sized = maxFileSize
    ? files.filter((file) => file.size <= maxFileSize)
    : files;
  if (files.length > 0 && sized.length === 0) {
    onError?.({
      code: "max_file_size",
      message: "All files exceed the maximum size.",
    });
  }
  return sized;
};

export const capFilesToCapacity = (
  files: File[],
  currentCount: number,
  maxFiles: number | undefined,
  onError?: PromptInputErrorHandler,
): File[] => {
  if (typeof maxFiles !== "number") {
    return files;
  }

  const capacity = Math.max(0, maxFiles - currentCount);
  const capped = files.slice(0, capacity);
  if (files.length > capacity) {
    onError?.({
      code: "max_files",
      message: "Too many files. Some were not added.",
    });
  }
  return capped;
};

export const mapFilesToUiParts = (
  files: File[],
): (FileUIPart & { id: string })[] =>
  files.map((file) => ({
    filename: file.name,
    id: nanoid(),
    mediaType: file.type,
    type: "file" as const,
    url: URL.createObjectURL(file),
  }));
