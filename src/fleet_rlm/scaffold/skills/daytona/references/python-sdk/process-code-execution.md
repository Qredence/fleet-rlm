## Contents

- Code execution
- Command execution
- Session operations
- Resource management
- Error handling
- Common issues




Daytona provides process and code execution capabilities through the `process` module in sandboxes.

## Code execution

Daytona provides methods to execute code in sandboxes. You can run code snippets in multiple languages with support for both stateless execution and stateful interpretation with persistent contexts.

- [Run code (stateless)](#run-code-stateless): run independent code snippets where each execution starts from a clean interpreter state; inherits the sandbox language that you choose at [sandbox creation](./sandboxes.md#create-sandboxes).
- [Run code (stateful)](#run-code-stateful): run code in a persistent interpreter context with variables, imports, and state to carry across executions; executes Python code and is available for every SDK.

### Run code (stateless)

Daytona provides methods to run code snippets in sandboxes using stateless execution. Each invocation starts from a clean interpreter, making it ideal for independent code snippets.

```python
# Run Python code
response = sandbox.process.code_run('''
def greet(name):
    return f"Hello, {name}!"

print(greet("Daytona"))
''')

print(response.result)
```

### Run code (stateful)

Daytona provides methods to run code with persistent state using the code interpreter. You can maintain variables and imports between calls, create isolated contexts, and control environment variables.

```python
from daytona import Daytona, OutputMessage

def handle_stdout(message: OutputMessage):
    print(f"[STDOUT] {message.output}")

daytona = Daytona()
sandbox = daytona.create()

# Shared default context
result = sandbox.code_interpreter.run_code(
    "counter = 1\nprint(f'Counter initialized at {counter}')",
    on_stdout=handle_stdout,
)

# Isolated context
ctx = sandbox.code_interpreter.create_context()
try:
    sandbox.code_interpreter.run_code(
        "value = 'stored in ctx'",
        context=ctx,
    )
    sandbox.code_interpreter.run_code(
        "print(value)",
        context=ctx,
        on_stdout=handle_stdout,
    )
finally:
    sandbox.code_interpreter.delete_context(ctx)
```

## Command execution

Daytona provides methods to execute shell commands in sandboxes. You can run commands with working directory, timeout, and environment variable options.

The working directory defaults to the sandbox working directory. It uses the WORKDIR specified in the Dockerfile if present, or falls back to the user's home directory if not (e.g., `workspace/repo` implies `/home/daytona/workspace/repo`). You can override it with an absolute path by starting the path with `/`.

### Execute commands

Daytona provides methods to execute shell commands in sandboxes by providing the command string and optional parameters for working directory, timeout, and environment variables. You can also use the `daytona exec` CLI command for quick command execution.

```python
# Execute any shell command
response = sandbox.process.exec("ls -la")
print(response.result)

# Setting a working directory and a timeout

response = sandbox.process.exec("sleep 3", cwd="workspace/src", timeout=5)
print(response.result)

# Passing environment variables

response = sandbox.process.exec("echo $CUSTOM_SECRET", env={
        "CUSTOM_SECRET": "DAYTONA"
    }
)
print(response.result)
```

## Session operations

Daytona provides methods to manage background process sessions in sandboxes. You can create sessions, execute commands, monitor status, and manage long-running processes.

### Get session status

Daytona provides methods to get session status and list all sessions in a sandbox by providing the session ID.

```python
# Check session's executed commands
session = sandbox.process.get_session(session_id)
print(f"Session {session_id}:")
for command in session.commands:
    print(f"Command: {command.command}, Exit Code: {command.exit_code}")

# List all running sessions

sessions = sandbox.process.list_sessions()
for session in sessions:
    print(f"Session: {session.session_id}, Commands: {session.commands}")
```

### Entrypoint session

Daytona provides methods to retrieve information about the internal entrypoint session in sandboxes. In each sandbox, the configured entrypoint command is executed inside a dedicated internal session, and you can fetch the session details (including the commands) and read its logs.

```python
# Entrypoint session details
session = sandbox.process.get_entrypoint_session()
print(f"Entrypoint session: {session.session_id}")
cmd = session.commands[0]
print(f"Entrypoint command id: {cmd.id}")
print(f"Command: {cmd.command}")

# Entrypoint logs (HTTP)
logs = sandbox.process.get_entrypoint_logs()
print(f"[STDOUT]: {logs.stdout}")
print(f"[STDERR]: {logs.stderr}")

# Stream entrypoint logs (WebSocket)
async def stream_entrypoint_logs():
    await sandbox.process.get_entrypoint_logs_async(
        lambda log: print(f"[STDOUT]: {log}"),
        lambda log: print(f"[STDERR]: {log}"),
    )

# Use asyncio.run in scripts; in notebooks or async apps, await stream_entrypoint_logs() instead.
asyncio.run(stream_entrypoint_logs())
```

### Execute interactive commands

Daytona provides methods to execute interactive commands in sessions. You can send input to running commands that expect user interaction, such as confirmations or interactive tools like database CLIs and package managers.

```python
session_id = "interactive-session"
sandbox.process.create_session(session_id)

# Execute command that requires confirmation
command = sandbox.process.execute_session_command(
    session_id,
    SessionExecuteRequest(
        command='pip uninstall requests',
        run_async=True,
    ),
)

# Stream logs asynchronously
logs_task = asyncio.create_task(
    sandbox.process.get_session_command_logs_async(
        session_id,
        command.cmd_id,
        lambda log: print(f"[STDOUT]: {log}"),
        lambda log: print(f"[STDERR]: {log}"),
    )
)

await asyncio.sleep(1)
# Send input to the command
sandbox.process.send_session_command_input(session_id, command.cmd_id, "y")

# Wait for logs to complete
await logs_task
```

## Resource management

Daytona provides methods to manage session resources. You should use sessions for long-running operations, clean up sessions after execution, and handle exceptions properly.

```python
# Python - Clean up session
session_id = "long-running-cmd"
try:
    sandbox.process.create_session(session_id)
    session = sandbox.process.get_session(session_id)
    # Do work...
finally:
    sandbox.process.delete_session(session.session_id)
```

## Error handling

Daytona provides methods to handle errors when executing processes. You should handle process exceptions properly, log error details for debugging, and use try-catch blocks for error handling.

```python
from daytona import DaytonaError

try:
    response = sandbox.process.code_run("invalid python code")
    if response.exit_code != 0:
        print(f"Exit code: {response.exit_code}")
        print(f"Error output: {response.result}")
except DaytonaError as e:
    print(f"Execution failed: {e}")
```

## Common issues

Daytona provides solutions for troubleshooting common issues related to process and code execution.

| **Issue**                | **Solutions**                                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- |
| Process execution failed | • Check command syntax<br/>• Verify required dependencies<br/>• Ensure sufficient permissions                   |
| Process timeout          | • Adjust timeout settings<br/>• Optimize long-running operations<br/>• Consider using background processes      |
| Resource limits          | • Monitor process memory usage<br/>• Handle process cleanup properly<br/>• Use appropriate resource constraints |
