import { FileText, X } from "lucide-react";

export interface AttachedFile {
  id: string;
  file: File;
  previewUrl?: string;
}

interface AttachmentChipProps {
  attachment: AttachedFile;
  onRemove: (id: string) => void;
}

function AttachmentChip({ attachment, onRemove }: AttachmentChipProps) {
  const isImage = attachment.file.type.startsWith("image/");

  return (
    <div className="relative group flex items-center gap-1.5 rounded-lg bg-accent/60 px-2 py-1 text-xs text-foreground shrink-0 max-w-[180px]">
      {isImage && attachment.previewUrl ? (
        <img
          src={attachment.previewUrl}
          alt={attachment.file.name}
          className="h-5 w-5 rounded object-cover shrink-0"
        />
      ) : (
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
      )}

      <span className="truncate">{attachment.file.name}</span>

      <button
        type="button"
        onClick={() => onRemove(attachment.id)}
        className="ml-auto flex items-center justify-center h-4 w-4 rounded-full bg-muted hover:bg-destructive hover:text-destructive-foreground transition-colors shrink-0"
        aria-label={`Remove ${attachment.file.name}`}
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export { AttachmentChip };
