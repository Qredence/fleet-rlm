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

```go
// Run code using shell command execution
// Note: For stateless code execution in Go, use ExecuteCommand with the appropriate interpreter
result, err := sandbox.Process.ExecuteCommand(ctx, `python3 -c '
def greet(name):
    return f"Hello, {name}!"

print(greet("Daytona"))
'`)
if err != nil {
	log.Fatal(err)
}
fmt.Println(result.Result)

// Run code with environment variables
result, err = sandbox.Process.ExecuteCommand(ctx, `python3 -c 'import os; print(f"FOO: {os.environ.get(\"FOO\")}")'`,
	options.WithCommandEnv(map[string]string{"FOO": "BAR"}),
)
if err != nil {
	log.Fatal(err)
}
fmt.Println(result.Result)

// Run code with timeout
result, err = sandbox.Process.ExecuteCommand(ctx, `python3 -c 'import time; time.sleep(2); print("Done")'`,
	options.WithExecuteTimeout(5*time.Second),
)
if err != nil {
	log.Fatal(err)
}
fmt.Println(result.Result)
```

### Run code (stateful)

Daytona provides methods to run code with persistent state using the code interpreter. You can maintain variables and imports between calls, create isolated contexts, and control environment variables.

```go
// Create a code interpreter context
ctxInfo, err := sandbox.CodeInterpreter.CreateContext(ctx, nil)
if err != nil {
	log.Fatal(err)
}
contextID := ctxInfo["id"].(string)

// Run code in the context
channels, err := sandbox.CodeInterpreter.RunCode(ctx,
	"counter = 1\nprint(f'Counter initialized at {counter}')",
	options.WithCustomContext(contextID),
)
if err != nil {
	log.Fatal(err)
}

// Read output
for msg := range channels.Stdout {
	fmt.Printf("[STDOUT] %s\n", msg.Text)
}

// Clean up context
err = sandbox.CodeInterpreter.DeleteContext(ctx, contextID)
if err != nil {
	log.Fatal(err)
}
```

## Command execution

Daytona provides methods to execute shell commands in sandboxes. You can run commands with working directory, timeout, and environment variable options.

The working directory defaults to the sandbox working directory. It uses the WORKDIR specified in the Dockerfile if present, or falls back to the user's home directory if not (e.g., `workspace/repo` implies `/home/daytona/workspace/repo`). You can override it with an absolute path by starting the path with `/`.

### Execute commands

Daytona provides methods to execute shell commands in sandboxes by providing the command string and optional parameters for working directory, timeout, and environment variables. You can also use the `daytona exec` CLI command for quick command execution.

```go
// Execute any shell command
response, err := sandbox.Process.ExecuteCommand(ctx, "ls -la")
if err != nil {
	log.Fatal(err)
}
fmt.Println(response.Result)

// Setting a working directory and a timeout
response, err = sandbox.Process.ExecuteCommand(ctx, "sleep 3",
	options.WithCwd("workspace/src"),
	options.WithExecuteTimeout(5*time.Second),
)
if err != nil {
	log.Fatal(err)
}
fmt.Println(response.Result)

// Passing environment variables
response, err = sandbox.Process.ExecuteCommand(ctx, "echo $CUSTOM_SECRET",
	options.WithCommandEnv(map[string]string{"CUSTOM_SECRET": "DAYTONA"}),
)
if err != nil {
	log.Fatal(err)
}
fmt.Println(response.Result)
```

## Session operations

Daytona provides methods to manage background process sessions in sandboxes. You can create sessions, execute commands, monitor status, and manage long-running processes.

### Get session status

Daytona provides methods to get session status and list all sessions in a sandbox by providing the session ID.

```go
// Check session's executed commands
session, err := sandbox.Process.GetSession(ctx, sessionID)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Session %s:\n", sessionID)
commands := session["commands"].([]any)
for _, cmd := range commands {
	cmdMap := cmd.(map[string]any)
	fmt.Printf("Command: %s, Exit Code: %v\n", cmdMap["command"], cmdMap["exitCode"])
}

// List all running sessions
sessions, err := sandbox.Process.ListSessions(ctx)
if err != nil {
	log.Fatal(err)
}
for _, sess := range sessions {
	fmt.Printf("Session: %s, Commands: %v\n", sess["sessionId"], sess["commands"])
}
```

### Entrypoint session

Daytona provides methods to retrieve information about the internal entrypoint session in sandboxes. In each sandbox, the configured entrypoint command is executed inside a dedicated internal session, and you can fetch the session details (including the commands) and read its logs.

```go
// Entrypoint session details
info, err := sandbox.Process.GetEntrypointSession(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Entrypoint session: %s\n", info.GetSessionId())
cmds := info.GetCommands()
cmd := cmds[0]
fmt.Printf("Entrypoint command id: %s\n", cmd.GetId())
fmt.Printf("Command: %s\n", cmd.GetCommand())

// Entrypoint logs (HTTP)
logs, err := sandbox.Process.GetEntrypointLogs(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Println(logs)

// Stream entrypoint logs (WebSocket)
stdout := make(chan string, 100)
stderr := make(chan string, 100)
go func() {
	for msg := range stderr {
		log.Printf("[STDERR]: %s", msg)
	}
}()
go func() {
	if err := sandbox.Process.GetEntrypointLogsStream(ctx, stdout, stderr); err != nil {
		log.Println("Entrypoint log stream error:", err)
	}
}()
for msg := range stdout {
	fmt.Printf("[STDOUT]: %s\n", msg)
}
```

### Execute interactive commands

Daytona provides methods to execute interactive commands in sessions. You can send input to running commands that expect user interaction, such as confirmations or interactive tools like database CLIs and package managers.

```go
sessionID := "interactive-session"
err := sandbox.Process.CreateSession(ctx, sessionID)
if err != nil {
	log.Fatal(err)
}

// Execute command that requires confirmation
result, err := sandbox.Process.ExecuteSessionCommand(ctx, sessionID, "pip uninstall requests", true)
if err != nil {
	log.Fatal(err)
}
cmdID := result["cmdId"].(string)

// Stream logs asynchronously
stdout := make(chan string)
stderr := make(chan string)

go func() {
	err := sandbox.Process.GetSessionCommandLogsStream(ctx, sessionID, cmdID, stdout, stderr)
	if err != nil {
		log.Println("Log stream error:", err)
	}
}()

time.Sleep(1 * time.Second)

// Note: SendSessionCommandInput is not available in Go SDK
// Use the API endpoint directly for sending input

// Read logs
for msg := range stdout {
	fmt.Printf("[STDOUT]: %s\n", msg)
}
```

## Resource management

Daytona provides methods to manage session resources. You should use sessions for long-running operations, clean up sessions after execution, and handle exceptions properly.

```go
// Go - Clean up session
sessionID := "long-running-cmd"
err := sandbox.Process.CreateSession(ctx, sessionID)
if err != nil {
	log.Fatal(err)
}
defer sandbox.Process.DeleteSession(ctx, sessionID)

session, err := sandbox.Process.GetSession(ctx, sessionID)
if err != nil {
	log.Fatal(err)
}
// Do work...
```

## Error handling

Daytona provides methods to handle errors when executing processes. You should handle process exceptions properly, log error details for debugging, and use try-catch blocks for error handling.

```go
result, err := sandbox.Process.ExecuteCommand(ctx, "python3 -c 'invalid python code'")
if err != nil {
	fmt.Println("Execution failed:", err)
}
if result != nil && result.ExitCode != 0 {
	fmt.Println("Exit code:", result.ExitCode)
	fmt.Println("Error output:", result.Result)
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
- [TypeScript SDK - process-code-execution](../typescript-sdk/process-code-execution.md)
