import { memo } from "react";
import { IconPuzzle } from "@tabler/icons-react";
import { TurnInputRowBase } from "./turn-input-row-base";

export type SkillsRowProps = {
  part: {
    input?: {
      label?: string;
      skills?: string[];
      preview?: string;
    };
  };
};

export const SkillsRow = memo(function SkillsRow({ part }: SkillsRowProps) {
  const label = part.input?.label || "Active skills";
  const skills = part.input?.skills || [];

  return (
    <TurnInputRowBase icon={<IconPuzzle className="w-full h-full" />} label={label}>
      {skills.length === 0 ? (
        <span className="text-sm text-muted-foreground">(none selected)</span>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {skills.map((skill) => (
            <span
              key={skill}
              className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-xs font-medium"
            >
              {skill}
            </span>
          ))}
        </div>
      )}
    </TurnInputRowBase>
  );
});
