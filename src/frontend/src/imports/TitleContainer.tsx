export default function TitleContainer() {
  return (
    <div
      className="content-stretch flex flex-col items-center justify-center pb-[5px] relative size-full whitespace-pre-wrap"
      data-name="Title Container"
    >
      <p
        className="leading-[40px] relative shrink-0 text-foreground tracking-[-0.5309px] w-full"
        style={{
          fontSize: "var(--text-display)",
          fontWeight: "var(--font-weight-medium)",
          fontFamily: "var(--font-family)",
        }}
      >
        Agentic Fleet Skill
      </p>
      <p
        className="leading-[normal] relative shrink-0 text-muted-foreground tracking-[-1.6px] w-full"
        style={{
          fontSize: "var(--text-display)",
          fontWeight: "var(--font-weight-regular)",
          fontFamily: "var(--font-family)",
        }}
      >
        What skill do you need?
      </p>
    </div>
  );
}
