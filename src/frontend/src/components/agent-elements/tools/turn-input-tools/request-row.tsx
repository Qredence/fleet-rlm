import { memo } from "react";
import { IconUser } from "@tabler/icons-react";
import { TurnInputRowBase } from "./turn-input-row-base";

export type RequestRowProps = {
  part: {
    input?: {
      label?: string;
      value?: string;
      preview?: string;
    };
  };
};

export const RequestRow = memo(function RequestRow({ part }: RequestRowProps) {
  const label = part.input?.label || "Request";
  const value = part.input?.value || "";

  return (
    <TurnInputRowBase icon={<IconUser className="w-full h-full" />} label={label}>
      <p className="text-sm font-semibold whitespace-pre-wrap break-words overflow-wrap">
        {value || "(empty)"}
      </p>
    </TurnInputRowBase>
  );
});
