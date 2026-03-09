/**
 * UI Components
 *
 * shadcn/ui components and custom UI primitives. All components follow
 * shadcn/ui conventions using forwardRef and data-slot attributes.
 *
 * Components are re-exported directly from their source files to maintain
 * backward compatibility with existing import patterns.
 */

// Layout & Structure
export { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "./accordion";
export { AspectRatio } from "./aspect-ratio";
export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent } from "./card";
export { Collapsible, CollapsibleTrigger, CollapsibleContent } from "./collapsible";
export { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "./resizable";
export { ScrollArea, ScrollBar } from "./scroll-area";
export { Separator } from "./separator";
export { Sheet, SheetTrigger, SheetContent, SheetHeader, SheetFooter, SheetTitle, SheetDescription } from "./sheet";
export { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupAction, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInput, SidebarInset, SidebarMenu, SidebarMenuAction, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarMenuSkeleton, SidebarMenuSub, SidebarMenuSubButton, SidebarMenuSubItem, SidebarProvider, SidebarRail, SidebarSeparator, SidebarTrigger } from "./sidebar";
// Note: useSidebar is available from sidebar.tsx but exported separately for hook usage
export { useSidebar } from "./sidebar";
export { Tabs, TabsList, TabsTrigger, TabsContent } from "./tabs";

// Navigation
export { Breadcrumb, BreadcrumbList, BreadcrumbItem, BreadcrumbLink, BreadcrumbPage, BreadcrumbSeparator, BreadcrumbEllipsis } from "./breadcrumb";
export { Menubar, MenubarMenu, MenubarTrigger, MenubarContent, MenubarItem, MenubarSeparator, MenubarLabel, MenubarCheckboxItem, MenubarRadioGroup, MenubarRadioItem, MenubarSub, MenubarSubTrigger, MenubarSubContent, MenubarShortcut } from "./menubar";
export { NavigationMenu, NavigationMenuList, NavigationMenuItem, NavigationMenuContent, NavigationMenuTrigger, NavigationMenuLink, NavigationMenuIndicator, NavigationMenuViewport } from "./navigation-menu";
export { Pagination, PaginationContent, PaginationEllipsis, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious } from "./pagination";

// Forms & Input
export { Button } from "./button";
export { ButtonGroup, ButtonGroupText, useButtonGroup, type ButtonGroupProps, type ButtonGroupTextProps } from "./button-group";
export { Checkbox } from "./checkbox";
export { Input } from "./input";
export { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput, InputGroupText, InputGroupTextarea } from "./input-group";
export { Label } from "./label";
export { RadioGroup, RadioGroupItem } from "./radio-group";
export { RadioOptionCard, type RadioOptionCardProps } from "./radio-option-card";
export { Select, SelectGroup, SelectValue, SelectTrigger, SelectContent, SelectLabel, SelectItem, SelectSeparator, SelectScrollUpButton, SelectScrollDownButton } from "./select";
export { SelectableCard, type SelectableCardProps } from "./selectable-card";
export { Slider } from "./slider";
export { Switch } from "./switch";
export { Textarea } from "./textarea";
export { Toggle } from "./toggle";
export { ToggleGroup, ToggleGroupItem } from "./toggle-group";

// Data Display
export { Alert, AlertTitle, AlertDescription } from "./alert";
export { Avatar, AvatarImage, AvatarFallback } from "./avatar";
export { Badge } from "./badge";
export { Progress } from "./progress";
export { Skeleton } from "./skeleton";
export { Table, TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption } from "./table";

// Feedback
export { AlertDialog, AlertDialogPortal, AlertDialogOverlay, AlertDialogTrigger, AlertDialogContent, AlertDialogHeader, AlertDialogFooter, AlertDialogTitle, AlertDialogDescription, AlertDialogAction, AlertDialogCancel } from "./alert-dialog";
export { Dialog, DialogPortal, DialogOverlay, DialogTrigger, DialogContent, DialogHeader, DialogFooter, DialogTitle, DialogDescription, DialogClose } from "./dialog";
export { Drawer, DrawerPortal, DrawerOverlay, DrawerTrigger, DrawerContent, DrawerHeader, DrawerFooter, DrawerTitle, DrawerDescription, DrawerClose } from "./drawer";
export { Spinner } from "./spinner";
export { Toaster } from "./sonner";

// Overlay
export { ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem, ContextMenuCheckboxItem, ContextMenuRadioItem, ContextMenuLabel, ContextMenuSeparator, ContextMenuShortcut, ContextMenuGroup, ContextMenuSub, ContextMenuSubTrigger, ContextMenuSubContent } from "./context-menu";
export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuCheckboxItem, DropdownMenuRadioItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuShortcut, DropdownMenuGroup, DropdownMenuSub, DropdownMenuSubTrigger, DropdownMenuSubContent } from "./dropdown-menu";
export { HoverCard, HoverCardTrigger, HoverCardContent } from "./hover-card";
export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor } from "./popover";
export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "./tooltip";

// Command & Search
export { Command, CommandDialog, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem, CommandShortcut, CommandSeparator } from "./command";

// Custom Components
export { AnimatedTabs, type AnimatedTabItem } from "./animated-tabs";
export { Carousel, CarouselContent, CarouselItem, type CarouselApi } from "./carousel";
export { IconButton, type IconButtonProps } from "./icon-button";
export { Queue, QueueSection, QueueSectionTrigger, QueueSectionLabel, QueueSectionContent, QueueList, QueueItem, QueueItemIndicator, QueueItemContent, QueueItemDescription } from "./queue";
export { Reasoning, type ReasoningProps, type ReasoningPart } from "./reasoning";
export { Streamdown, type StreamdownProps } from "./streamdown";
export { SuggestionChip, type SuggestionChipProps } from "./suggestion-chip";

// Utilities
export { cn } from "@/lib/utils/cn";
export { buttonVariants, type ButtonVariantProps } from "./button-variants";
export { badgeVariants, type BadgeVariantProps } from "./badge-variants";
export { toggleVariants, type ToggleVariantProps } from "./toggle-variants";
