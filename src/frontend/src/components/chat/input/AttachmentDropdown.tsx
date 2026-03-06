import { useRef, type ChangeEvent } from "react";
import { AtSign, Paperclip, Plus } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface AttachmentDropdownProps {
  onFilesSelected?: (files: File[]) => void;
  uploadsEnabled?: boolean;
  onUnsupportedSelect?: () => void;
}

function AttachmentDropdown({
  onFilesSelected,
  uploadsEnabled = true,
  onUnsupportedSelect,
}: AttachmentDropdownProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0 && onFilesSelected) {
      onFilesSelected(Array.from(files));
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.csv"
        className="hidden"
        onChange={handleFileChange}
      />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <span className="inline-flex">
            <IconButton
              type="button"
              aria-label="Prompt features"
              className="touch-target rounded-full"
            >
              <Plus className="size-5 text-foreground" />
            </IconButton>
          </span>
        </DropdownMenuTrigger>

        <DropdownMenuContent
          align="start"
          className="w-56 border-border bg-popover"
        >
          <DropdownMenuItem
            className="gap-2.5 py-2.5 px-3 rounded-lg cursor-pointer"
            onClick={() => {
              if (!uploadsEnabled) {
                onUnsupportedSelect?.();
                return;
              }
              fileInputRef.current?.click();
            }}
          >
            <Paperclip className="h-4 w-4" />
            <span className="text-sm">
              {uploadsEnabled
                ? "Add images, PDFs or CSVs"
                : "Add images, PDFs or CSVs (coming soon)"}
            </span>
          </DropdownMenuItem>

          <DropdownMenuItem
            className="gap-2.5 py-2.5 px-3 rounded-lg cursor-not-allowed opacity-60"
            disabled
          >
            <AtSign className="h-4 w-4" />
            <span className="text-sm">Add context (coming soon)</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

export { AttachmentDropdown };
