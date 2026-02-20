import { typo } from "../../components/config/typo";

interface UserMessageProps {
  content: string;
}

/**
 * UserMessage — right-aligned user chat bubble.
 */
export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end mb-4">
      <div
        className="max-w-[85%] md:max-w-md rounded-card bg-secondary px-6 py-4 shadow-sm border border-border-subtle"
        style={typo.labelRegular}
      >
        {content}
      </div>
    </div>
  );
}
