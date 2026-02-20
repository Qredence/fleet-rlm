import svgPaths from "@/imports/svg-er4mz3hmp1";

function Icon() {
  return (
    <div className="relative shrink-0 size-[20px]" data-name="Icon">
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 20 20"
      >
        <g id="Icon">
          <path d={svgPaths.p2b835b70} fill="var(--foreground)" id="vector" />
        </g>
      </svg>
    </div>
  );
}

function ComposerActionFalseFalseFalse() {
  return (
    <div
      className="bg-transparent content-stretch flex items-center justify-center min-h-[34px] min-w-[34px] px-[8px] relative rounded-[26px] shrink-0 size-[34px]"
      data-name="_Composer-action/false/false/false"
    >
      <Icon />
    </div>
  );
}

function LeftContent() {
  return (
    <div
      className="content-stretch flex items-center relative shrink-0"
      data-name="Left Content"
    >
      <ComposerActionFalseFalseFalse />
    </div>
  );
}

function Content1() {
  return (
    <div
      className="content-stretch flex flex-[1_0_0] items-center min-h-px min-w-px relative"
      data-name="Content"
    >
      <p
        className="flex-[1_0_0] min-h-px min-w-px relative text-muted-foreground whitespace-pre-wrap"
        style={{
          fontSize: "var(--text-base)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "24px",
          letterSpacing: "-0.32px",
        }}
      >
        Ask anything
      </p>
    </div>
  );
}

function Icon1() {
  return (
    <div className="relative shrink-0 size-[20px]" data-name="Icon">
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 20 20"
      >
        <g id="Icon">
          <path d={svgPaths.p3c9c8b00} fill="var(--foreground)" id="vector" />
        </g>
      </svg>
    </div>
  );
}

function ComposerActionFalseFalseFalse1() {
  return (
    <div
      className="bg-transparent content-stretch flex items-center justify-center min-h-[34px] min-w-[34px] px-[8px] relative rounded-[26px] shrink-0 size-[34px]"
      data-name="_Composer-action/false/false/false"
    >
      <Icon1 />
    </div>
  );
}

function IconThinSendBold() {
  return (
    <div
      className="relative shrink-0 size-[20px]"
      data-name="icon-thin/send-bold"
    >
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 20 20"
      >
        <g id="icon-thin/send-bold">
          <path
            d={svgPaths.p22cb5880}
            fill="var(--primary-foreground)"
            id="vector"
          />
        </g>
      </svg>
    </div>
  );
}

function ComposerActionSend() {
  return (
    <div
      className="bg-foreground content-stretch flex items-center justify-center p-[4px] relative rounded-[999px] shrink-0 size-[36px]"
      data-name="_Composer-action/Send"
    >
      <IconThinSendBold />
    </div>
  );
}

function RightContent() {
  return (
    <div
      className="content-stretch flex gap-[6px] items-center relative shrink-0"
      data-name="Right Content"
    >
      <ComposerActionFalseFalseFalse1 />
      <ComposerActionSend />
    </div>
  );
}

function Content() {
  return (
    <div className="relative shrink-0 w-full" data-name="Content">
      <div className="flex flex-row items-center size-full">
        <div className="content-stretch flex gap-[10px] items-center p-[10px] relative w-full">
          <LeftContent />
          <Content1 />
          <RightContent />
        </div>
      </div>
    </div>
  );
}

function Composer() {
  return (
    <div
      className="bg-background relative rounded-[28px] shrink-0 w-full"
      data-name="Composer"
    >
      <div className="content-stretch flex flex-col items-start overflow-clip relative rounded-[inherit] w-full">
        <Content />
      </div>
      <div
        aria-hidden="true"
        className="absolute border border-border border-solid inset-0 pointer-events-none rounded-[28px] shadow-sm"
      />
    </div>
  );
}

export default function ComposeFooter() {
  return (
    <div
      className="bg-background content-stretch flex flex-col gap-[8px] items-start pb-[8px] relative rounded-tl-[28px] rounded-tr-[28px] size-full"
      data-name="Compose/Footer"
    >
      <Composer />
      <p
        className="relative shrink-0 text-muted-foreground text-center w-full whitespace-pre-wrap"
        style={{
          fontSize: "var(--text-helper)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "16px",
        }}
      >
        Agentic Fleet is in preview and can do mistakes.
      </p>
    </div>
  );
}
