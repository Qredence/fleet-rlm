import { AnimatePresence, motion } from "motion/react";
import {
  Archive,
  ChevronRight,
  Database,
  FileCode,
  FileCog,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  HardDrive,
} from "lucide-react";
import { typo } from "@/lib/config/typo";
import type { FsNode } from "@/lib/data/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";
import { countFiles, formatDate, formatFileSize } from "@/lib/volumes/browser";

function fileIcon(name: string, _mime?: string) {
  if (name.endsWith(".md")) {
    return <FileText className="w-3.5 h-3.5 text-chart-2" />;
  }
  if (name.endsWith(".py")) {
    return <FileCode className="w-3.5 h-3.5 text-chart-1" />;
  }
  if (name.endsWith(".yaml") || name.endsWith(".yml")) {
    return <FileCog className="w-3.5 h-3.5 text-chart-4" />;
  }
  if (name.endsWith(".json") || name.endsWith(".jsonl")) {
    return <FileJson className="w-3.5 h-3.5 text-chart-3" />;
  }
  if (name.endsWith(".tar.gz") || name.endsWith(".zip")) {
    return <Archive className="w-3.5 h-3.5 text-muted-foreground" />;
  }
  if (name.endsWith(".bin") || name.endsWith(".db")) {
    return <Database className="w-3.5 h-3.5 text-chart-5" />;
  }
  return <FileText className="w-3.5 h-3.5 text-muted-foreground" />;
}

export function FsItem({
  node,
  depth,
  expanded,
  onToggle,
  onSelectFile,
  isMobile,
  prefersReduced,
}: {
  node: FsNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelectFile: (node: FsNode) => void;
  isMobile?: boolean;
  prefersReduced?: boolean | null;
}) {
  const isOpen = expanded.has(node.id);
  const isExpandable = node.type !== "file" && (node.children?.length ?? 0) > 0;
  const isVolume = node.type === "volume";
  const isFile = node.type === "file";

  return (
    <div>
      <Button
        variant="ghost"
        className={cn(
          "w-full justify-start gap-2 rounded-lg h-auto px-3",
          isMobile ? "py-3 touch-target" : "py-2",
          isVolume && "bg-muted/50",
        )}
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        onClick={() => {
          if (isFile) {
            onSelectFile(node);
          } else {
            onToggle(node.id);
          }
        }}
      >
        {isExpandable ? (
          <motion.div
            animate={{ rotate: isOpen ? 90 : 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.15, ease: "easeOut" }
            }
          >
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
          </motion.div>
        ) : (
          <div className="w-3.5 h-3.5" />
        )}

        {isVolume ? (
          <HardDrive
            className={cn(
              "w-4 h-4",
              isOpen ? "text-accent" : "text-muted-foreground",
            )}
          />
        ) : isFile ? (
          fileIcon(node.name, node.mime)
        ) : isOpen ? (
          <FolderOpen className="w-4 h-4 text-accent" />
        ) : (
          <Folder className="w-4 h-4 text-muted-foreground" />
        )}

        <span
          className="flex-1 text-left min-w-0 truncate text-foreground"
          style={isVolume ? typo.label : typo.caption}
        >
          {isVolume ? `/sandbox/${node.name}` : node.name}
        </span>

        {isFile && node.size ? (
          <span
            className="text-muted-foreground shrink-0"
            style={{
              ...typo.micro,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {formatFileSize(node.size)}
          </span>
        ) : isVolume ? (
          <Badge variant="secondary" className="rounded-full shrink-0">
            <span style={typo.micro}>{countFiles(node)} files</span>
          </Badge>
        ) : null}

        {node.modifiedAt && (
          <span
            className="text-muted-foreground shrink-0 hidden md:inline"
            style={typo.micro}
          >
            {formatDate(node.modifiedAt)}
          </span>
        )}
      </Button>

      <AnimatePresence>
        {isOpen && node.children && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={
              prefersReduced
                ? { duration: 0.01 }
                : { duration: 0.18, ease: "easeOut" }
            }
            className="overflow-hidden"
          >
            {node.children.map((child) => (
              <FsItem
                key={child.id}
                node={child}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
                onSelectFile={onSelectFile}
                isMobile={isMobile}
                prefersReduced={prefersReduced}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
