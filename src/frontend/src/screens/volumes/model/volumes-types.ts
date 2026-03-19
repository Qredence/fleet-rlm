export type FsNodeType = "volume" | "directory" | "file";

export interface FsNode {
  id: string;
  name: string;
  path: string;
  type: FsNodeType;
  children?: FsNode[];
  size?: number;
  mime?: string;
  modifiedAt?: string;
  skillId?: string;
}
