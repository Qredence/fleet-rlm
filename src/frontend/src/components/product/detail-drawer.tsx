/**
 * DetailDrawer — Slide-over detail panel built on the shadcn Sheet.
 *
 * Provides a right-side drawer with header (title + actions), a scrollable
 * content area, and an optional footer.
 *
 * ```tsx
 * <DetailDrawer
 *   open={isOpen}
 *   onOpenChange={setIsOpen}
 *   title="Run Details"
 *   description="Inspect this optimization run."
 *   actions={<Button size="xs">Export</Button>}
 *   footer={<Button onClick={close}>Close</Button>}
 * >
 *   <PropertyList>…</PropertyList>
 * </DetailDrawer>
 * ```
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from "@/components/ui/sheet";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface DetailDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  /** Optional action nodes rendered in the header beside the title. */
  actions?: ReactNode;
  /** Optional sticky footer content. */
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function DetailDrawer({
  open,
  onOpenChange,
  title,
  description,
  actions,
  footer,
  children,
  className,
}: DetailDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className={cn("flex flex-col sm:max-w-md md:max-w-lg", className)}
      >
        <SheetHeader className="flex flex-row items-start justify-between gap-4 pr-10">
          <div className="flex flex-col gap-1.5">
            <SheetTitle>{title}</SheetTitle>
            {description ? <SheetDescription>{description}</SheetDescription> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </SheetHeader>

        <div className="flex-1 overflow-y-auto overscroll-contain px-4">{children}</div>

        {footer ? <SheetFooter>{footer}</SheetFooter> : null}
      </SheetContent>
    </Sheet>
  );
}
