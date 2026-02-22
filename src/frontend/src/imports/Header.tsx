import svgPaths from "@/imports/svg-g9pgfbkia";

function LogoContainer() {
  return (
    <div className="relative shrink-0" data-name="Logo Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex gap-[8px] items-center relative">
        <div className="h-[17px] relative shrink-0 w-[18px]" data-name="Vector">
          <svg
            className="block size-full"
            fill="none"
            preserveAspectRatio="none"
            viewBox="0 0 18 17"
          >
            <g id="Vector">
              <path
                clipRule="evenodd"
                d={svgPaths.p4dc2a80}
                fill="var(--foreground)"
                fillRule="evenodd"
              />
            </g>
          </svg>
        </div>
        <p
          className="leading-[27px] relative shrink-0 text-foreground text-center"
          style={{
            fontSize: "var(--text-label)",
            fontWeight: "var(--font-weight-medium)",
            fontFamily: "var(--font-family)",
            letterSpacing: "-0.4395px",
          }}
        >
          Qredence
        </p>
      </div>
    </div>
  );
}

function LucideToolCase() {
  return (
    <div
      className="content-stretch flex gap-[8px] h-[24px] items-center overflow-clip py-[3px] relative shrink-0"
      data-name="lucide/tool-case"
    >
      <p
        className="overflow-hidden relative shrink-0 text-foreground text-ellipsis"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "20px",
          letterSpacing: "-0.3304px",
        }}
      >
        Dashboard
      </p>
    </div>
  );
}

function Frame() {
  return (
    <div
      className="content-stretch flex gap-[8px] items-center relative shrink-0"
      data-name="Frame"
    >
      <LucideToolCase />
    </div>
  );
}

function DefaultState() {
  return (
    <div
      className="content-stretch flex h-[36px] items-center px-[8px] relative rounded-[10px] shrink-0"
      data-name="Default State"
    >
      <Frame />
    </div>
  );
}

function LucideToolCase1() {
  return (
    <div
      className="content-stretch flex gap-[8px] h-[24px] items-center overflow-clip py-[3px] relative shrink-0"
      data-name="lucide/tool-case"
    >
      <p
        className="overflow-hidden relative shrink-0 text-foreground text-ellipsis"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "20px",
          letterSpacing: "-0.3304px",
        }}
      >
        Chat
      </p>
    </div>
  );
}

function LeadingIcon() {
  return (
    <div
      className="content-stretch flex gap-[8px] items-center justify-center relative shrink-0"
      data-name="leading-icon"
    >
      <LucideToolCase1 />
    </div>
  );
}

function NavItemComponent() {
  return (
    <div
      className="content-stretch flex h-[36px] items-center justify-center px-[8px] relative rounded-[10px] shrink-0"
      data-name="nav-item-component"
    >
      <LeadingIcon />
    </div>
  );
}

function LucideToolCase2() {
  return (
    <div
      className="content-stretch flex gap-[8px] h-[24px] items-center overflow-clip py-[3px] relative shrink-0"
      data-name="lucide/tool-case"
    >
      <p
        className="overflow-hidden relative shrink-0 text-foreground text-ellipsis"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "20px",
          letterSpacing: "-0.3304px",
        }}
      >
        Skills
      </p>
    </div>
  );
}

function LeadingIcon1() {
  return (
    <div
      className="content-stretch flex gap-[8px] items-center justify-center relative shrink-0"
      data-name="leading-icon"
    >
      <LucideToolCase2 />
    </div>
  );
}

function NavItemComponent1() {
  return (
    <div
      className="content-stretch flex h-[36px] items-center justify-center px-[8px] relative rounded-[10px] shrink-0"
      data-name="nav-item-component"
    >
      <LeadingIcon1 />
    </div>
  );
}

function LucideToolCase3() {
  return (
    <div
      className="content-stretch flex gap-[8px] h-[24px] items-center overflow-clip py-[3px] relative shrink-0"
      data-name="lucide/tool-case"
    >
      <p
        className="overflow-hidden relative shrink-0 text-foreground text-ellipsis"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "20px",
          letterSpacing: "-0.3304px",
        }}
      >
        Taxonomy
      </p>
    </div>
  );
}

function LeadingIcon2() {
  return (
    <div
      className="content-stretch flex gap-[8px] items-center justify-center relative shrink-0"
      data-name="leading-icon"
    >
      <LucideToolCase3 />
    </div>
  );
}

function NavItemComponent2() {
  return (
    <div
      className="content-stretch flex h-[36px] items-center justify-center px-[8px] relative rounded-[10px] shrink-0"
      data-name="nav-item-component"
    >
      <LeadingIcon2 />
    </div>
  );
}

function LucideToolCase4() {
  return (
    <div
      className="content-stretch flex gap-[8px] h-[24px] items-center overflow-clip py-[3px] relative shrink-0"
      data-name="lucide/tool-case"
    >
      <p
        className="overflow-hidden relative shrink-0 text-foreground text-ellipsis"
        style={{
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "20px",
          letterSpacing: "-0.3304px",
        }}
      >
        Analytics
      </p>
    </div>
  );
}

function LeadingIcon3() {
  return (
    <div
      className="content-stretch flex gap-[8px] items-center justify-center relative shrink-0"
      data-name="leading-icon"
    >
      <LucideToolCase4 />
    </div>
  );
}

function NavItemComponent3() {
  return (
    <div
      className="content-stretch flex h-[36px] items-center justify-center px-[8px] relative rounded-[10px] shrink-0"
      data-name="nav-item-component"
    >
      <LeadingIcon3 />
    </div>
  );
}

function Navigation() {
  return (
    <div className="relative shrink-0" data-name="Navigation">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex gap-[8px] items-center relative">
        <DefaultState />
        <NavItemComponent />
        <NavItemComponent1 />
        <NavItemComponent2 />
        <NavItemComponent3 />
      </div>
    </div>
  );
}

function IconChatTools() {
  return (
    <div className="relative shrink-0 size-[24px]" data-name="icon-chat-tools">
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 24 24"
      >
        <g id="Compose Edit Square">
          <path d={svgPaths.p32b8ef00} fill="var(--foreground)" id="vector" />
        </g>
      </svg>
    </div>
  );
}

function NewSkillButton() {
  return (
    <div
      className="content-stretch flex items-center p-[4px] relative shrink-0"
      data-name="new-skill-button"
    >
      <IconChatTools />
    </div>
  );
}

function IconMiscellaneous() {
  return (
    <div
      className="relative shrink-0 size-[24px]"
      data-name="icon-miscellaneous"
    >
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 24 24"
      >
        <g id="icon / analyze-data">
          <g id="vector">
            <path d={svgPaths.p3a006380} fill="var(--foreground)" />
            <path d={svgPaths.p20a25d00} fill="var(--foreground)" />
            <path d={svgPaths.p1d95d480} fill="var(--foreground)" />
            <path d={svgPaths.pe8ed000} fill="var(--foreground)" />
            <path d={svgPaths.p2032bd00} fill="var(--foreground)" />
          </g>
        </g>
      </svg>
    </div>
  );
}

function SidePanelButton() {
  return (
    <div
      className="content-stretch flex items-center p-[4px] relative shrink-0"
      data-name="side-panel-button"
    >
      <IconMiscellaneous />
    </div>
  );
}

function Icon() {
  return (
    <div className="relative shrink-0 size-[24px]" data-name="icon">
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 24 24"
      >
        <g id="icon / settings-cog">
          <g id="vector">
            <path
              clipRule="evenodd"
              d={svgPaths.p2a673a00}
              fill="var(--foreground)"
              fillRule="evenodd"
            />
            <path
              clipRule="evenodd"
              d={svgPaths.p1a718200}
              fill="var(--foreground)"
              fillRule="evenodd"
            />
          </g>
        </g>
      </svg>
    </div>
  );
}

function SettingButton() {
  return (
    <div
      className="content-stretch flex items-center p-[4px] relative shrink-0"
      data-name="setting-button"
    >
      <Icon />
    </div>
  );
}

function Actions() {
  return (
    <div className="relative shrink-0" data-name="Actions">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex gap-[8px] items-center justify-end relative">
        <NewSkillButton />
        <SidePanelButton />
        <SettingButton />
      </div>
    </div>
  );
}

export default function Header() {
  return (
    <div
      className="content-stretch flex items-center justify-between pl-[32px] pr-[42px] py-[24px] relative size-full"
      data-name="Header"
    >
      <LogoContainer />
      <Navigation />
      <Actions />
    </div>
  );
}
