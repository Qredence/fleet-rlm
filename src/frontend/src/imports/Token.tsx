import svgPaths from "./svg-14cpcjf8e9";

function Icon() {
  return (
    <div className="relative shrink-0 size-[16px]" data-name="Icon">
      <svg
        className="block size-full"
        fill="none"
        preserveAspectRatio="none"
        viewBox="0 0 16 16"
      >
        <g id="Icon">
          <path d={svgPaths.p36b52f00} fill="var(--accent)" id="vector" />
        </g>
      </svg>
    </div>
  );
}

export default function Token() {
  return (
    <div
      className="bg-transparent content-stretch flex gap-[8px] items-center px-[16px] py-[12px] relative rounded-[25px] size-full"
      data-name="Token"
    >
      <div
        aria-hidden="true"
        className="absolute border border-border border-solid inset-0 pointer-events-none rounded-[25px]"
      />
      <Icon />
      <div
        className="flex flex-col justify-center leading-[0] max-w-[508px] opacity-90 relative shrink-0 text-foreground whitespace-nowrap"
        style={{
          fontSize: "var(--text-caption)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
          lineHeight: "1.4",
          letterSpacing: "-0.08px",
        }}
      >
        <p className="leading-[18px]">Help me write</p>
      </div>
    </div>
  );
}
