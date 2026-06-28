## Contents

- Code execution
- Command execution
- Session operations
- Resource management
- Error handling
- Common issues
- See Also




Daytona provides process and code execution capabilities through the `process` module in sandboxes.

## Code execution

Daytona provides methods to execute code in sandboxes. You can run code snippets in multiple languages with support for both stateless execution and stateful interpretation with persistent contexts.

- [Run code (stateless)](#run-code-stateless): run independent code snippets where each execution starts from a clean interpreter state; inherits the sandbox language that you choose at [sandbox creation](./sandboxes.md#create-sandboxes).
- [Run code (stateful)](#run-code-stateful): run code in a persistent interpreter context with variables, imports, and state to carry across executions; executes Python code and is available for every SDK.

### Run code (stateless)

Daytona provides methods to run code snippets in sandboxes using stateless execution. Each invocation starts from a clean interpreter, making it ideal for independent code snippets.

```typescript
// Run TypeScript code
let response = await sandbox.process.codeRun(`
function greet(name: string): string {
    return \`Hello, \${name}!\`;
}

console.log(greet("Daytona"));
`);
console.log(response.result);

// Run code with argv and environment variables
response = await sandbox.process.codeRun(
    `
    console.log(\`Hello, \${process.argv[2]}!\`);
    console.log(\`FOO: \${process.env.FOO}\`);
    `,
    {
      argv: ["Daytona"],
      env: { FOO: "BAR" }
    }
);
console.log(response.result);

// Run code with timeout (5 seconds)
response = await sandbox.process.codeRun(
    'setTimeout(() => console.log("Done"), 2000);',
    undefined,
    5
);
console.log(response.result);
```

### Run code (stateful)

Daytona provides methods to run code with persistent state using the code interpreter. You can maintain variables and imports between calls, create isolated contexts, and control environment variables.

```typescript
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona()

async function main() {
    const sandbox = await daytona.create()

    // Shared default context
    await sandbox.codeInterpreter.runCode(
`
counter = 1
print(f'Counter initialized at {counter}')
`,
        { onStdout: (msg) => process.stdout.write(`[STDOUT] ${msg.output}`)},
    )

    // Isolated context
    const ctx = await sandbox.codeInterpreter.createContext()
    try {
    await sandbox.codeInterpreter.runCode(
        `value = 'stored in ctx'`,
        { context: ctx },
    )
    await sandbox.codeInterpreter.runCode(
        `print(value)`,
        { context: ctx, onStdout: (msg) => process.stdout.write(`[STDOUT] ${msg.output}`) },
    )
    } finally {
    await sandbox.codeInterpreter.deleteContext(ctx)
    }
}

main()
```

## Command execution

Daytona provides methods to execute shell commands in sandboxes. You can run commands with working directory, timeout, and environment variable options.

The working directory defaults to the sandbox working directory. It uses the WORKDIR specified in the Dockerfile if present, or falls back to the user's home directory if not (e.g., `workspace/repo` implies `/home/daytona/workspace/repo`). You can override it with an absolute path by starting the path with `/`.

### Execute commands

Daytona provides methods to execute shell commands in sandboxes by providing the command string and optional parameters for working directory, timeout, and environment variables. You can also use the `daytona exec` CLI command for quick command execution.

```typescript

// Execute any shell command
const response = await sandbox.process.executeCommand("ls -la");
console.log(response.result);

// Setting a working directory and a timeout
const response2 = await sandbox.process.executeCommand("sleep 3", "workspace/src", undefined, 5);
console.log(response2.result);

// Passing environment variables
const response3 = await sandbox.process.executeCommand("echo $CUSTOM_SECRET", ".", {
        "CUSTOM_SECRET": "DAYTONA"
    }
);
console.log(response3.result);
```

## Session operations

Daytona provides methods to manage background process sessions in sandboxes. You can create sessions, execute commands, monitor status, and manage long-running processes.

### Get session status

Daytona provides methods to get session status and list all sessions in a sandbox by providing the session ID.

```typescript
// Check session's executed commands
const session = await sandbox.process.getSession(sessionId);
console.log(`Session ${sessionId}:`);
for (const command of session.commands) {
    console.log(`Command: ${command.command}, Exit Code: ${command.exitCode}`);
}

// List all running sessions
const sessions = await sandbox.process.listSessions();
for (const session of sessions) {
    console.log(`Session: ${session.sessionId}, Commands: ${session.commands}`);
}
```

### Entrypoint session

Daytona provides methods to retrieve information about the internal entrypoint session in sandboxes. In each sandbox, the configured entrypoint command is executed inside a dedicated internal session, and you can fetch the session details (including the commands) and read its logs.

```typescript
// Entrypoint session details
const session = await sandbox.process.getEntrypointSession();
console.log(`Entrypoint session: ${session.sessionId}`);
const cmd = session.commands[0]
console.log(`Entrypoint command id: ${cmd.id}`);
console.log(`Command: ${cmd.command}`);

// Entrypoint logs (HTTP)
const logs = await sandbox.process.getEntrypointLogs();
console.log('[STDOUT]:', logs.stdout);
console.log('[STDERR]:', logs.stderr);

// Stream entrypoint logs (WebSocket)
await sandbox.process.getEntrypointLogs(
    (chunk) => console.log('[STDOUT]:', chunk),
    (chunk) => console.log('[STDERR]:', chunk),
);
```

### Execute interactive commands

Daytona provides methods to execute interactive commands in sessions. You can send input to running commands that expect user interaction, such as confirmations or interactive tools like database CLIs and package managers.

```typescript
const sessionId = 'interactive-session'
await sandbox.process.createSession(sessionId)

// Execute command that requires confirmation
const command = await sandbox.process.executeSessionCommand(sessionId, {
    command: 'pip uninstall requests',
    runAsync: true,
})

// Stream logs asynchronously
const logPromise = sandbox.process.getSessionCommandLogs(
    sessionId,
    command.cmdId!,
    (stdout) => console.log('[STDOUT]:', stdout),
    (stderr) => console.log('[STDERR]:', stderr),
)

await new Promise((resolve) => setTimeout(resolve, 1000))
// Send input to the command
await sandbox.process.sendSessionCommandInput(sessionId, command.cmdId!, 'y')

// Wait for logs to complete
await logPromise
```

## Resource management

Daytona provides methods to manage session resources. You should use sessions for long-running operations, clean up sessions after execution, and handle exceptions properly.

```typescript
// TypeScript - Clean up session
const sessionId = "long-running-cmd";
try {
    await sandbox.process.createSession(sessionId);
    const session = await sandbox.process.getSession(sessionId);
    // Do work...
} finally {
    await sandbox.process.deleteSession(session.sessionId);
}
```

## Error handling

Daytona provides methods to handle errors when executing processes. You should handle process exceptions properly, log error details for debugging, and use try-catch blocks for error handling.

```typescript
import { DaytonaError } from '@daytona/sdk'

try {
    const response = await sandbox.process.codeRun("invalid typescript code");
    if (response.exitCode !== 0) {
        console.error("Exit code:", response.exitCode);
        console.error("Error output:", response.result);
    }
} catch (e) {
    if (e instanceof DaytonaError) {
        console.error("Execution failed:", e);
    }
}
```

## Common issues

Daytona provides solutions for troubleshooting common issues related to process and code execution.

| **Issue**                | **Solutions**                                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- |
| Process execution failed | • Check command syntax<br/>• Verify required dependencies<br/>• Ensure sufficient permissions                   |
| Process timeout          | • Adjust timeout settings<br/>• Optimize long-running operations<br/>• Consider using background processes      |
| Resource limits          | • Monitor process memory usage<br/>• Handle process cleanup properly<br/>• Use appropriate resource constraints |

## See Also
- [Python SDK - process-code-execution](../python-sdk/process-code-execution.md)
