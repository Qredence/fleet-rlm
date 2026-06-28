## Contents

- Create PTY session
- Connect to PTY session
- List PTY sessions
- Get PTY session info
- Kill PTY session
- Resize PTY session
- Interactive commands
- Long-running processes
- Resource management
- PtyHandle methods
- Error handling
- Troubleshooting
- See Also




Daytona provides powerful pseudo terminal (PTY) capabilities through the `process` module in sandboxes. PTY sessions allow you to create interactive terminal sessions that can execute commands, handle user input, and manage terminal operations.

A PTY (Pseudo Terminal) is a virtual terminal interface that allows programs to interact with a shell as if they were connected to a real terminal. PTY sessions in Daytona enable:

- **Interactive Development**: REPLs, debuggers, and development tools
- **Build Processes**: Running and monitoring compilation, testing, or deployment
- **System Administration**: Remote server management and configuration
- **User Interfaces**: Terminal-based applications requiring user interaction

## Create PTY session

Daytona provides methods to create an interactive terminal session that can execute commands and handle user input.

```python
from daytona.common.pty import PtySize

pty_handle = sandbox.process.create_pty_session(
    id="my-session",
    cwd="/workspace",
    envs={"TERM": "xterm-256color"},
    pty_size=PtySize(cols=120, rows=30)
)
```

## Connect to PTY session

Daytona provides methods to establish a connection to an existing PTY session, enabling interaction with a previously created terminal.

```python
pty_handle = sandbox.process.connect_pty_session("my-session")
```

## List PTY sessions

Daytona provides methods to list PTY sessions, allowing you to retrieve information about all PTY sessions, both active and inactive, that have been created in the sandbox.

```python
# List all PTY sessions
sessions = sandbox.process.list_pty_sessions()

for session in sessions:
    print(f"Session ID: {session.id}")
    print(f"Active: {session.active}")
    print(f"Created: {session.created_at}")
```

## Get PTY session info

Daytona provides methods to get information about a specific PTY session, allowing you to retrieve comprehensive information about a specific PTY session including its current state, configuration, and metadata.

```python
# Get details about a specific PTY session
session_info = sandbox.process.get_pty_session_info("my-session")

print(f"Session ID: {session_info.id}")
print(f"Active: {session_info.active}")
print(f"Working Directory: {session_info.cwd}")
print(f"Terminal Size: {session_info.cols}x{session_info.rows}")
```

## Kill PTY session

Daytona provides methods to kill a PTY session, allowing you to forcefully terminate a PTY session and cleans up all associated resources.

```python
# Kill a specific PTY session
sandbox.process.kill_pty_session("my-session")

# Verify the session no longer exists
pty_sessions = sandbox.process.list_pty_sessions()
for pty_session in pty_sessions:
    print(f"PTY session: {pty_session.id}")
```

## Resize PTY session

Daytona provides methods to resize a PTY session, allowing you to change the terminal dimensions of an active PTY session. This sends a SIGWINCH signal to the shell process, allowing terminal applications to adapt to the new size.

```python
from daytona.common.pty import PtySize

# Resize a PTY session to a larger terminal
new_size = PtySize(rows=40, cols=150)
updated_info = sandbox.process.resize_pty_session("my-session", new_size)

print(f"Terminal resized to {updated_info.cols}x{updated_info.rows}")

# You can also use the PtyHandle's resize method
pty_handle.resize(new_size)
```

## Interactive commands

Daytona provides methods to handle interactive commands with PTY sessions, allowing you to handle interactive commands that require user input and can be resized during execution.

```python
import time
from daytona import Daytona, Sandbox
from daytona.common.pty import PtySize

def handle_pty_data(data: bytes):
    text = data.decode("utf-8", errors="replace")
    print(text, end="")

# Create PTY session
pty_handle = sandbox.process.create_pty_session(
    id="interactive-session",
    pty_size=PtySize(cols=300, rows=100)
)

# Send interactive command
pty_handle.send_input('printf "Are you accepting the terms and conditions? (y/n): " && read confirm && if [ "$confirm" = "y" ]; then echo "You accepted"; else echo "You did not accept"; fi\n')
time.sleep(1)
pty_handle.send_input("y\n")

# Resize terminal
pty_session_info = pty_handle.resize(PtySize(cols=210, rows=110))
print(f"PTY session resized to {pty_session_info.cols}x{pty_session_info.rows}")

# Exit the session
pty_handle.send_input('exit\n')

# Handle output using iterator
for data in pty_handle:
    handle_pty_data(data)

print(f"Session completed with exit code: {pty_handle.exit_code}")
```

## Long-running processes

Daytona provides methods to manage long-running processes with PTY sessions, allowing you to manage long-running processes that need to be monitored or terminated.

```python
import time
import threading
from daytona import Daytona, Sandbox
from daytona.common.pty import PtySize

def handle_pty_data(data: bytes):
    text = data.decode("utf-8", errors="replace")
    print(text, end="")

# Create PTY session
pty_handle = sandbox.process.create_pty_session(
    id="long-running-session",
    pty_size=PtySize(cols=120, rows=30)
)

# Start a long-running process
pty_handle.send_input('while true; do echo "Running... $(date)"; sleep 1; done\n')

# Using thread and wait() method to handle PTY output
thread = threading.Thread(target=pty_handle.wait, args=(handle_pty_data, 10))
thread.start()

time.sleep(3)  # Let it run for a bit

print("Killing long-running process...")
pty_handle.kill()

thread.join()

print(f"\nProcess terminated with exit code: {pty_handle.exit_code}")
if pty_handle.error:
    print(f"Termination reason: {pty_handle.error}")
```

## Resource management

Daytona provides methods to manage resource leaks with PTY sessions, allowing you to always clean up PTY sessions to prevent resource leaks.

```python
# Python: Use try/finally
pty_handle = None
try:
    pty_handle = sandbox.process.create_pty_session(id="session", pty_size=PtySize(cols=120, rows=30))
    # Do work...
finally:
    if pty_handle:
        pty_handle.kill()
```

## PtyHandle methods

Daytona provides methods to interact with PTY sessions, allowing you to send input, resize the terminal, wait for completion, and manage the WebSocket connection to a PTY session.

### Send input

Daytona provides methods to send input to a PTY session, allowing you to send input data (keystrokes or commands) to the PTY session.

```python
# Send a command
pty_handle.send_input("ls -la\n")

# Send user input
pty_handle.send_input("y\n")
```

### Wait for completion

Daytona provides methods to wait for a PTY process to exit and return the result, allowing you to wait for a PTY process to exit and return the result.

```python
# Wait with a callback for output data
def handle_data(data: bytes):
    print(data.decode("utf-8", errors="replace"), end="")

result = pty_handle.wait(on_data=handle_data, timeout=30)
print(f"Exit code: {result.exit_code}")
```

### Wait for connection

Daytona provides methods to wait for the WebSocket connection to be established before sending input, allowing you to wait for the WebSocket connection to be established before sending input.

```python
# Python handles connection internally during creation
# No explicit wait needed
```

### Kill PTY process

Daytona provides methods to kill a PTY process and terminate the session from the handle.

```python
pty_handle.kill()
```

### Resize from handle

Daytona provides methods to resize the PTY terminal dimensions directly from the handle.

```python
from daytona.common.pty import PtySize

pty_handle.resize(PtySize(cols=120, rows=30))
```

### Disconnect

Daytona provides methods to disconnect from a PTY session and clean up resources without killing the process.

```python
# Python: Use kill() to terminate, or let the handle go out of scope
```

### Check connection status

Daytona provides methods to check if a PTY session is still connected.

```python
# Python: Check by attempting operations or using session info
session_info = sandbox.process.get_pty_session_info("my-session")
print(f"Session active: {session_info.active}")
```

### Exit code and error

Daytona provides methods to access the exit code and error message after a PTY process terminates.

```python
# After iteration or wait completes
print(f"Exit code: {pty_handle.exit_code}")
if pty_handle.error:
    print(f"Error: {pty_handle.error}")
```

### Iterate over output (Python)

Daytona provides methods to iterate over a PTY handle to receive output data.

```python
# Iterate over PTY output
for data in pty_handle:
    text = data.decode("utf-8", errors="replace")
    print(text, end="")

print(f"Session ended with exit code: {pty_handle.exit_code}")
```

## Error handling

Daytona provides methods to monitor exit codes and handle errors appropriately with PTY sessions.

```python
# Python: Check exit codes
result = pty_handle.wait()
if result.exit_code != 0:
    print(f"Command failed: {result.exit_code}")
    print(f"Error: {result.error}")
```

## Troubleshooting

- **Connection issues**: verify sandbox status, network connectivity, and proper session IDs
- **Performance issues**: use appropriate terminal dimensions and efficient data handlers
- **Process management**: use explicit `kill()` calls and proper timeout handling for long-running processes

## See Also
- [TypeScript SDK - pty](../typescript-sdk/pty.md)
