import { useState } from "react";
import { motion } from "motion/react";
import { Check, X } from "lucide-react";
import { toast } from "sonner";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import type { MemoryType } from "@/lib/data/types";
import { CREATABLE_TYPES, TYPE_META } from "@/lib/memory/metadata";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/components/ui/utils";

export interface NewMemoryFormProps {
  onSubmit: (data: {
    type: MemoryType;
    content: string;
    tags: string[];
  }) => void;
  onCancel: () => void;
  isMobile?: boolean;
  reduced?: boolean | null;
}

export function NewMemoryForm({
  onSubmit,
  onCancel,
  isMobile,
  reduced,
}: NewMemoryFormProps) {
  const [type, setType] = useState<MemoryType>("fact");
  const [content, setContent] = useState("");
  const [tagsStr, setTagsStr] = useState("");

  const handleSubmit = () => {
    if (!content.trim()) {
      toast.error("Content is required");
      return;
    }
    const tags = tagsStr
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
    onSubmit({ type, content: content.trim(), tags });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: reduced ? 0 : -8, height: 0 }}
      animate={{ opacity: 1, y: 0, height: "auto" }}
      exit={{ opacity: 0, y: reduced ? 0 : -8, height: 0 }}
      transition={reduced ? springs.instant : springs.default}
      className="overflow-hidden"
    >
      <Card className="border-accent/30 bg-accent/[0.02]">
        <CardContent className={cn("p-4 space-y-3", isMobile && "p-3")}>
          <div className="flex items-center justify-between">
            <span className="text-foreground" style={typo.label}>
              New Memory Entry
            </span>
            <Button
              variant="ghost"
              className={cn("h-7 w-7 p-0", isMobile && "touch-target")}
              onClick={onCancel}
              aria-label="Cancel"
            >
              <X className="w-4 h-4 text-muted-foreground" />
            </Button>
          </div>

          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Type
            </label>
            <Select value={type} onValueChange={(v) => setType(v as MemoryType)}>
              <SelectTrigger
                className={cn("w-full", isMobile && "touch-target")}
                style={typo.label}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CREATABLE_TYPES.map((t) => {
                  const meta = TYPE_META[t];
                  const MIcon = meta.icon;
                  return (
                    <SelectItem key={t} value={t}>
                      <div className="flex items-center gap-2">
                        <MIcon className={cn("w-3.5 h-3.5", meta.color)} />
                        <span>{meta.label}</span>
                      </div>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Content
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="What should the agent remember?"
              rows={3}
              className={cn(
                "w-full resize-none rounded-lg border border-border-subtle bg-background p-3 text-foreground",
                "placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring",
                isMobile && "min-h-[88px]",
              )}
              style={{
                fontFamily: "var(--font-family)",
                fontSize: "var(--text-caption)",
                fontWeight: "var(--font-weight-regular)",
                lineHeight: "1.5",
              }}
              autoFocus
            />
          </div>

          <div>
            <label
              className="text-muted-foreground mb-1.5 block"
              style={typo.helper}
            >
              Tags <span className="text-muted-foreground/60">(comma-separated)</span>
            </label>
            <Input
              value={tagsStr}
              onChange={(e) => setTagsStr(e.target.value)}
              placeholder="testing, policy, preference"
              className={cn(isMobile && "touch-target")}
              style={typo.caption}
            />
          </div>

          <div className="flex items-center gap-2 pt-1">
            <Button
              variant="default"
              className={cn("gap-1.5 rounded-button", isMobile && "touch-target")}
              onClick={handleSubmit}
              disabled={!content.trim()}
            >
              <Check className="w-4 h-4" />
              <span style={typo.label}>Save</span>
            </Button>
            <Button
              variant="ghost"
              className={cn("rounded-button", isMobile && "touch-target")}
              onClick={onCancel}
            >
              <span style={typo.label}>Cancel</span>
            </Button>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}
