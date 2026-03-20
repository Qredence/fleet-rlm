import { useRef, type ChangeEvent } from "react";
import { AtSign, Paperclip, Plus } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import {
  Menubar,
  MenubarContent,
  MenubarItem,
  MenubarMenu,
  MenubarTrigger,
} from "@/components/ui/menubar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";
import {
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_VARIANT,
  PROMPT_INPUT_MENUBAR_CLASSNAME,
  PROMPT_INPUT_MENU_CONTENT_CLASSNAME,
} from "./composerActionStyles";

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

      <Menubar className={PROMPT_INPUT_MENUBAR_CLASSNAME}>
        <MenubarMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <MenubarTrigger asChild>
                  <IconButton
                    type="button"
                    aria-label="Prompt features"
                    className={PROMPT_INPUT_ICON_BUTTON_CLASSNAME}
                    size="icon-sm"
                    variant={PROMPT_INPUT_ICON_BUTTON_VARIANT}
                  >
                    <Plus className="size-4" />
                  </IconButton>
                </MenubarTrigger>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={6}>
              Prompt features
            </TooltipContent>
          </Tooltip>

          <MenubarContent align="start" className={cn(PROMPT_INPUT_MENU_CONTENT_CLASSNAME, "w-60")}>
            <MenubarItem
              className="prompt-composer-menu-item cursor-pointer gap-3 rounded-xl px-3 py-2.5"
              onSelect={() => {
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
            </MenubarItem>

            <MenubarItem
              className="prompt-composer-menu-item prompt-composer-menu-item-muted cursor-not-allowed gap-3 rounded-xl px-3 py-2.5 opacity-70"
              disabled
            >
              <AtSign className="h-4 w-4" />
              <span className="text-sm">Add context (coming soon)</span>
            </MenubarItem>
          </MenubarContent>
        </MenubarMenu>
      </Menubar>
    </>
  );
}

export { AttachmentDropdown };
