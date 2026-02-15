import { z } from "zod";
import { readdir } from "node:fs/promises";
import { join } from "node:path";

export const InfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  location: z.string(),
  content: z.string().optional(),
});

export type Info = z.infer<typeof InfoSchema>;

interface SkillFile {
  path: string;
  dir: string;
  name: string;
}

export class Skill {
  private static skillDirs = [
    ".claude/skills",
    ".agents/skills",
    ".opencode/skill",
  ];

  static async list(projectDir: string = "."): Promise<Info[]> {
    const skills: Info[] = [];
    const seen = new Set<string>();

    const homeDir = process.env.HOME || process.env.USERPROFILE || ".";
    const globalDirs = this.skillDirs.map((d) => join(homeDir, d));
    const projectDirs = this.skillDirs.map((d) => join(projectDir, d));

    const allDirs = [...globalDirs, ...projectDirs];

    for (const dir of allDirs) {
      const found = await this.scanDirectory(dir);
      for (const skill of found) {
        if (!seen.has(skill.name)) {
          seen.add(skill.name);
          skills.push(skill);
        }
      }
    }

    return skills.sort((a, b) => a.name.localeCompare(b.name));
  }

  static async load(name: string, projectDir: string = "."): Promise<Info | null> {
    const skills = await this.list(projectDir);
    const skill = skills.find((s) => s.name === name);
    if (!skill) return null;

    const content = await this.readSkillContent(skill.location);
    return {
      ...skill,
      content,
    };
  }

  static async search(query: string, projectDir: string = "."): Promise<Info[]> {
    const skills = await this.list(projectDir);
    const lowerQuery = query.toLowerCase();

    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(lowerQuery) ||
        s.description.toLowerCase().includes(lowerQuery)
    );
  }

  private static async scanDirectory(dir: string): Promise<Info[]> {
    const skills: Info[] = [];

    try {
      const entries = await readdir(dir, { withFileTypes: true });

      for (const entry of entries) {
        if (entry.isDirectory()) {
          const skillDir = join(dir, entry.name);
          const skillFile = join(skillDir, "SKILL.md");

          const file = Bun.file(skillFile);
          if (await file.exists()) {
            const content = await file.text();
            const parsed = this.parseSkillContent(content, entry.name);

            skills.push({
              name: parsed.name || entry.name,
              description: parsed.description,
              location: skillDir,
            });
          }
        } else if (entry.name.endsWith(".md")) {
          const skillFile = join(dir, entry.name);
          const file = Bun.file(skillFile);

          if (await file.exists()) {
            const content = await file.text();
            const name = entry.name.replace(/\.md$/, "");
            const parsed = this.parseSkillContent(content, name);

            skills.push({
              name: parsed.name || name,
              description: parsed.description,
              location: skillFile,
            });
          }
        }
      }
    } catch {
      // Directory doesn't exist or can't be read
    }

    return skills;
  }

  private static parseSkillContent(
    content: string,
    defaultName: string
  ): { name: string; description: string } {
    const lines = content.split("\n");
    let name = defaultName;
    let description = "";

    const titleMatch = content.match(/^#\s+(.+)$/m);
    if (titleMatch) {
      name = titleMatch[1]!.trim();
    }

    let foundDescription = false;
    for (const line of lines) {
      const trimmed = line.trim();

      if (trimmed.startsWith("#") && !trimmed.startsWith("##")) {
        continue;
      }

      if (trimmed.startsWith("##")) {
        if (foundDescription) break;
        continue;
      }

      if (trimmed.length > 0 && !foundDescription) {
        description = trimmed;
        foundDescription = true;
      } else if (foundDescription && description.length < 200) {
        description += " " + trimmed;
      }
    }

    if (description.length > 200) {
      description = description.slice(0, 197) + "...";
    }

    if (!description) {
      description = `Skill located at ${defaultName}`;
    }

    return { name, description };
  }

  private static async readSkillContent(location: string): Promise<string> {
    const file = Bun.file(location);
    if (await file.exists()) {
      return await file.text();
    }

    const skillMd = join(location, "SKILL.md");
    const skillFile = Bun.file(skillMd);
    if (await skillFile.exists()) {
      return await skillFile.text();
    }

    return "";
  }
}
