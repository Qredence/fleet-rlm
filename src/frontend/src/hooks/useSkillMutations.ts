/**
 * React Query mutations for skill CRUD operations.
 *
 * Provides create, update, and delete mutations with optimistic updates
 * and automatic cache invalidation. In mock mode, mutations are
 * simulated locally with a short delay.
 *
 * @example
 * ```tsx
 * const { createSkill, updateSkill, deleteSkill } = useSkillMutations();
 *
 * createSkill.mutate({ name: 'new-skill', displayName: 'New Skill', ... });
 * updateSkill.mutate({ id: 'sk-001', data: { status: 'published' } });
 * deleteSkill.mutate('sk-001');
 * ```
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { usePostHog } from "@posthog/react";
import { isMockMode } from "@/lib/api/config";
import { taskEndpoints } from "@/lib/api/endpoints";
import { adaptTask } from "@/lib/api/adapters";
import { createLocalId } from "@/lib/id";
import { skillKeys } from "@/hooks/useSkills";
import type { Skill } from "@/lib/data/types";

// ── Types ───────────────────────────────────────────────────────────

export interface CreateSkillInput {
  name: string;
  displayName: string;
  domain: string;
  category: string;
  description: string;
  tags?: string[];
  dependencies?: string[];
  taxonomyPath?: string;
}

export interface UpdateSkillInput {
  id: string;
  data: Partial<
    Pick<
      Skill,
      | "displayName"
      | "description"
      | "status"
      | "tags"
      | "dependencies"
      | "taxonomyPath"
      | "version"
    >
  >;
}

// ── Mock helpers ────────────────────────────────────────────────────

function mockDelay(ms = 600): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function createMockSkill(input: CreateSkillInput): Skill {
  const id = createLocalId("sk-mock");
  return {
    id,
    name: input.name,
    displayName: input.displayName,
    version: "1.0.0",
    domain: input.domain,
    category: input.category,
    status: "draft",
    description: input.description,
    tags: input.tags ?? [],
    dependencies: input.dependencies ?? [],
    taxonomyPath:
      input.taxonomyPath ?? `/${input.domain}/${input.category}/${input.name}`,
    usageCount: 0,
    lastUsed: new Date().toISOString(),
    qualityScore: 0,
    author: "current-user",
    createdAt: new Date().toISOString(),
  };
}

// ── Hook ────────────────────────────────────────────────────────────

export function useSkillMutations() {
  const queryClient = useQueryClient();
  const posthog = usePostHog();
  const mock = isMockMode();

  // ── Create ──────────────────────────────────────────────────────
  const createSkill = useMutation({
    mutationFn: async (input: CreateSkillInput): Promise<Skill> => {
      if (mock) {
        await mockDelay();
        return createMockSkill(input);
      }
      const response = await taskEndpoints.create(
        input as unknown as Record<string, unknown>,
      );
      return adaptTask(response as Parameters<typeof adaptTask>[0]);
    },
    onSuccess: (newSkill) => {
      // Add to cache
      queryClient.setQueryData<Skill[]>(skillKeys.lists(), (old) => {
        return old ? [...old, newSkill] : [newSkill];
      });
      queryClient.invalidateQueries({ queryKey: skillKeys.all });
      toast.success(`Created "${newSkill.displayName}"`);

      // PostHog: Track skill creation
      posthog?.capture("skill_created", {
        skill_id: newSkill.id,
        skill_name: newSkill.displayName,
        skill_domain: newSkill.domain,
        skill_category: newSkill.category,
      });
    },
    onError: (error) => {
      toast.error(`Failed to create skill: ${error.message}`);
    },
  });

  // ── Update ──────────────────────────────────────────────────────
  const updateSkill = useMutation({
    mutationFn: async ({ id, data }: UpdateSkillInput): Promise<Skill> => {
      if (mock) {
        await mockDelay();
        // Return merged data
        const existing = queryClient.getQueryData<Skill[]>(skillKeys.lists());
        const found = existing?.find((s) => s.id === id);
        if (!found) throw new Error("Skill not found");
        return { ...found, ...data };
      }
      const response = await taskEndpoints.update(
        id,
        data as unknown as Record<string, unknown>,
      );
      return adaptTask(response as Parameters<typeof adaptTask>[0]);
    },
    // Optimistic update
    onMutate: async ({ id, data }) => {
      await queryClient.cancelQueries({ queryKey: skillKeys.all });
      const previousSkills = queryClient.getQueryData<Skill[]>(
        skillKeys.lists(),
      );

      queryClient.setQueryData<Skill[]>(skillKeys.lists(), (old) => {
        return old?.map((s) => (s.id === id ? { ...s, ...data } : s));
      });

      return { previousSkills };
    },
    onError: (_error, _variables, context) => {
      // Rollback on error
      if (context?.previousSkills) {
        queryClient.setQueryData(skillKeys.lists(), context.previousSkills);
      }
      toast.error(`Failed to update skill: ${_error.message}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: skillKeys.all });
    },
    onSuccess: (skill) => {
      toast.success(`Updated "${skill.displayName}"`);
    },
  });

  // ── Delete ──────────────────────────────────────────────────────
  const deleteSkill = useMutation({
    mutationFn: async (id: string): Promise<string> => {
      if (mock) {
        await mockDelay();
        return id;
      }
      await taskEndpoints.delete(id);
      return id;
    },
    // Optimistic removal
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: skillKeys.all });
      const previousSkills = queryClient.getQueryData<Skill[]>(
        skillKeys.lists(),
      );

      queryClient.setQueryData<Skill[]>(skillKeys.lists(), (old) => {
        return old?.filter((s) => s.id !== id);
      });

      return { previousSkills };
    },
    onError: (_error, _id, context) => {
      if (context?.previousSkills) {
        queryClient.setQueryData(skillKeys.lists(), context.previousSkills);
      }
      toast.error(`Failed to delete skill: ${_error.message}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: skillKeys.all });
    },
    onSuccess: (deletedId) => {
      toast.success("Skill deleted");

      // PostHog: Track skill deletion
      posthog?.capture("skill_deleted", { skill_id: deletedId });
    },
  });

  return { createSkill, updateSkill, deleteSkill };
}
