export type FsNodeType = "volume" | "directory" | "file";
export type VolumeProvider = "modal" | "daytona";

export interface FsNode {
  id: string;
  name: string;
  path: string;
  provider?: VolumeProvider;
  type: FsNodeType;
  children?: FsNode[];
  size?: number;
  mime?: string;
  modifiedAt?: string;
  skillId?: string;
}
