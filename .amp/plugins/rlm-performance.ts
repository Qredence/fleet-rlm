import type { PluginAPI } from '@ampcode/plugin'

export const description =
	'Adds a safe local validation command for Fleet DSPy RLM and Daytona broker performance changes.'

const FOCUSED_TEST_PATHS = [
	'tests/unit/backend/test_host_tool_submit_broker.py',
	'tests/unit/backend/daytona/test_broker.py',
	'tests/unit/backend/daytona/test_interpreter_tracing.py',
	'tests/unit/backend/rlm/test_program_guidance.py',
	'tests/unit/backend/rlm/test_program_instructions.py',
	'tests/unit/backend/test_config.py',
	'tests/unit/backend/test_config_policy.py',
	'tests/unit/scripts/test_run_rlm_latency.py',
].join(' ')

const FOCUSED_SOURCE_PATHS = [
	'src/fleet_rlm/daytona/broker.py',
	'src/fleet_rlm/daytona/interpreter.py',
	'src/fleet_rlm/rlm/program.py',
].join(' ')

const FOCUSED_CHECK = [
	`uv run pytest ${FOCUSED_TEST_PATHS} -q`,
	`uv run ruff check ${FOCUSED_SOURCE_PATHS} ${FOCUSED_TEST_PATHS}`,
	`uv run ruff format --check ${FOCUSED_SOURCE_PATHS} ${FOCUSED_TEST_PATHS}`,
	'uv run ty check src',
].join(' && ')

export default function (amp: PluginAPI) {
	amp.registerCommand(
		'fleet-rlm-performance-check',
		{
			title: 'Run focused RLM performance checks',
			category: 'Fleet-RLM',
			description:
				'Runs focused broker/RLM tests, lint, format, and type checks without enabling live credentials.',
		},
		async (ctx) => {
			const workspaceRoot = amp.system.workspaceRoot
			if (!workspaceRoot) {
				await ctx.ui.notify('Fleet-RLM performance checks require an open workspace.')
				return
			}

			const rootPath = amp.helpers.filePathFromURI(workspaceRoot)
			try {
				const result = await ctx.$`cd ${rootPath} && bash -lc ${FOCUSED_CHECK}`
				const output = result.stdout.trim().split('\n').slice(-8).join('\n')
				await ctx.ui.notify(`Fleet-RLM focused checks passed.\n${output}`)
			} catch (error) {
				const message = error instanceof Error ? error.message : String(error)
				await ctx.ui.notify(`Fleet-RLM focused checks failed.\n${message.slice(0, 1200)}`)
			}
		},
	)
}
