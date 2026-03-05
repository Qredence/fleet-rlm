import {
  createContext,
  useContext,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import {
  AudioLines,
  FileText,
  Image as ImageIcon,
  Link as LinkIcon,
  Video,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/utils/cn";

type AttachmentVariant = "grid" | "inline" | "list";
type MediaCategory =
  | "image"
  | "video"
  | "audio"
  | "document"
  | "source"
  | "unknown";

export interface AttachmentData {
  id: string;
  name?: string;
  title?: string;
  url?: string;
  previewUrl?: string;
  mimeType?: string;
  mediaType?: string;
  description?: string;
  sizeBytes?: number;
  kind?: string;
}

interface AttachmentItemContextValue {
  data: AttachmentData;
  onRemove?: () => void;
}

const AttachmentItemContext = createContext<AttachmentItemContextValue | null>(
  null,
);

function getMediaCategory(data: AttachmentData): MediaCategory {
  const mime = String(data.mimeType ?? data.mediaType ?? "").toLowerCase();
  const kind = String(data.kind ?? "").toLowerCase();
  const url = String(data.previewUrl ?? data.url ?? "").toLowerCase();

  if (kind === "source") return "source";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (
    mime.includes("pdf") ||
    mime.includes("text") ||
    mime.includes("json") ||
    mime.includes("markdown")
  ) {
    return "document";
  }

  if (/\.(png|jpg|jpeg|gif|webp|svg)$/.test(url)) return "image";
  if (/\.(mp4|webm|mov|m4v)$/.test(url)) return "video";
  if (/\.(mp3|wav|ogg|m4a|flac)$/.test(url)) return "audio";
  if (/\.(pdf|txt|md|json|csv|yaml|yml)$/.test(url)) return "document";
  return "unknown";
}

function fallbackLabelByCategory(category: MediaCategory): string {
  if (category === "image") return "Image";
  if (category === "video") return "Video";
  if (category === "audio") return "Audio";
  if (category === "source") return "Source";
  if (category === "document") return "Document";
  return "Attachment";
}

function getAttachmentLabel(data: AttachmentData): string {
  const direct = String(data.name ?? data.title ?? "").trim();
  if (direct) return direct;

  const url = String(data.url ?? data.previewUrl ?? "").trim();
  if (url) {
    const seg = url.split("/").filter(Boolean).pop();
    if (seg) return seg;
  }

  return fallbackLabelByCategory(getMediaCategory(data));
}

function mediaTypeLabel(data: AttachmentData): string {
  const mime = String(data.mimeType ?? data.mediaType ?? "").trim();
  if (mime) return mime;
  return getMediaCategory(data);
}

function sizeLabel(sizeBytes: number | undefined): string | null {
  if (!Number.isFinite(sizeBytes) || !sizeBytes || sizeBytes <= 0) return null;
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fallbackIconForCategory(category: MediaCategory) {
  if (category === "image") return <ImageIcon className="size-4" />;
  if (category === "video") return <Video className="size-4" />;
  if (category === "audio") return <AudioLines className="size-4" />;
  if (category === "source") return <LinkIcon className="size-4" />;
  return <FileText className="size-4" />;
}

function Attachments({
  variant = "grid",
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { variant?: AttachmentVariant }) {
  return (
    <div
      data-slot="attachments"
      data-variant={variant}
      className={cn(
        variant === "grid" && "grid grid-cols-1 gap-2 sm:grid-cols-2",
        variant === "inline" && "flex flex-wrap items-center gap-1.5",
        variant === "list" && "space-y-2",
        className,
      )}
      {...props}
    />
  );
}

function Attachment({
  data,
  onRemove,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  data: AttachmentData;
  onRemove?: () => void;
}) {
  return (
    <AttachmentItemContext.Provider value={{ data, onRemove }}>
      <div
        data-slot="attachment"
        className={cn(
          "group relative rounded-lg border border-border-subtle bg-card p-2",
          className,
        )}
        {...props}
      />
    </AttachmentItemContext.Provider>
  );
}

function useAttachmentContext(): AttachmentItemContextValue {
  const ctx = useContext(AttachmentItemContext);
  if (!ctx) {
    throw new Error(
      "Attachment subcomponent must be used within <Attachment />",
    );
  }
  return ctx;
}

function AttachmentPreview({
  fallbackIcon,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { fallbackIcon?: ReactNode }) {
  const { data } = useAttachmentContext();
  const category = getMediaCategory(data);
  const previewUrl = data.previewUrl ?? data.url;

  return (
    <div
      data-slot="attachment-preview"
      className={cn(
        "mb-2 flex min-h-10 items-center justify-center rounded-md border border-border-subtle bg-muted/30",
        className,
      )}
      {...props}
    >
      {category === "image" && previewUrl ? (
        <img
          src={previewUrl}
          alt={getAttachmentLabel(data)}
          className="h-20 w-full rounded-md object-cover"
          loading="lazy"
        />
      ) : category === "video" && previewUrl ? (
        <video
          src={previewUrl}
          className="h-20 w-full rounded-md object-cover"
          muted
          playsInline
          preload="metadata"
        />
      ) : (
        <span className="text-muted-foreground">
          {fallbackIcon ?? fallbackIconForCategory(category)}
        </span>
      )}
    </div>
  );
}

function AttachmentInfo({
  showMediaType = false,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { showMediaType?: boolean }) {
  const { data } = useAttachmentContext();
  const label = getAttachmentLabel(data);
  const media = mediaTypeLabel(data);
  const size = sizeLabel(data.sizeBytes);

  return (
    <div
      data-slot="attachment-info"
      className={cn("space-y-0.5", className)}
      {...props}
    >
      <div
        className="truncate text-xs font-medium text-foreground"
        title={label}
      >
        {label}
      </div>
      {showMediaType || size ? (
        <div className="truncate text-[11px] text-muted-foreground">
          {[showMediaType ? media : null, size].filter(Boolean).join(" • ")}
        </div>
      ) : null}
    </div>
  );
}

function AttachmentRemove({
  label = "Remove attachment",
  className,
  ...props
}: React.ComponentProps<typeof Button> & { label?: string }) {
  const { onRemove } = useAttachmentContext();
  if (!onRemove) return null;

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={onRemove}
      aria-label={label}
      className={cn(
        "absolute right-1 top-1 size-6 opacity-0 transition-opacity group-hover:opacity-100",
        className,
      )}
      {...props}
    >
      <X className="size-3.5" />
    </Button>
  );
}

function AttachmentHoverCard({
  children,
  ...props
}: React.ComponentProps<typeof HoverCard>) {
  return <HoverCard {...props}>{children}</HoverCard>;
}

function AttachmentHoverCardTrigger({
  children,
  ...props
}: React.ComponentProps<typeof HoverCardTrigger>) {
  return <HoverCardTrigger {...props}>{children}</HoverCardTrigger>;
}

function AttachmentHoverCardContent({
  children,
  ...props
}: React.ComponentProps<typeof HoverCardContent>) {
  return <HoverCardContent {...props}>{children}</HoverCardContent>;
}

function AttachmentEmpty({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="attachment-empty"
      className={cn(
        "rounded-md border border-dashed border-border-subtle p-3 text-xs text-muted-foreground",
        className,
      )}
      {...props}
    >
      No attachments
    </div>
  );
}

export {
  Attachments,
  Attachment,
  AttachmentPreview,
  AttachmentInfo,
  AttachmentRemove,
  AttachmentHoverCard,
  AttachmentHoverCardTrigger,
  AttachmentHoverCardContent,
  AttachmentEmpty,
};
