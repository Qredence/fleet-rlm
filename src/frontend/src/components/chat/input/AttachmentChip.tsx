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
    <div className="prompt-composer-attachment-chip group relative flex max-w-55 shrink-0 items-center gap-2 rounded-full px-2.5 py-1.5 text-xs">
      {isImage && attachment.previewUrl ? (
        <img
          src={attachment.previewUrl}
          alt={attachment.file.name}
          className="h-5 w-5 shrink-0 rounded-full object-cover"
        />
      ) : (
        <FileText className="prompt-composer-attachment-chip-icon h-4 w-4 shrink-0" />
      )}

      <span className="truncate">{attachment.file.name}</span>

      <button
        type="button"
        onClick={() => onRemove(attachment.id)}
        className="prompt-composer-attachment-chip-remove ml-auto flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
        aria-label={`Remove ${attachment.file.name}`}
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export { AttachmentChip };
