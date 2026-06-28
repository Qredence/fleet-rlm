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

```go
// Create a PTY session with custom configuration
handle, err := sandbox.Process.CreatePty(ctx, "my-interactive-session",
	options.WithCreatePtySize(types.PtySize{Cols: 120, Rows: 30}),
	options.WithCreatePtyEnv(map[string]string{"TERM": "xterm-256color"}),
)
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

// Wait for connection to be established
if err := handle.WaitForConnection(ctx); err != nil {
	log.Fatal(err)
}

// Send commands to the terminal
handle.SendInput([]byte("ls -la\n"))
handle.SendInput([]byte("echo 'Hello, PTY!'\n"))
handle.SendInput([]byte("exit\n"))

// Read output from channel
for data := range handle.DataChan() {
	fmt.Print(string(data))
}

// Wait for completion and get result
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("PTY session completed with exit code: %d\n", *result.ExitCode)
```

## Connect to PTY session

Daytona provides methods to establish a connection to an existing PTY session, enabling interaction with a previously created terminal.

```go
// Connect to an existing PTY session
handle, err := sandbox.Process.ConnectPty(ctx, "my-session")
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

// Wait for connection to be established
if err := handle.WaitForConnection(ctx); err != nil {
	log.Fatal(err)
}

// Send commands to the existing session
handle.SendInput([]byte("pwd\n"))
handle.SendInput([]byte("ls -la\n"))
handle.SendInput([]byte("exit\n"))

// Read output
for data := range handle.DataChan() {
	fmt.Print(string(data))
}

// Wait for completion
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Session exited with code: %d\n", *result.ExitCode)
```

## List PTY sessions

Daytona provides methods to list PTY sessions, allowing you to retrieve information about all PTY sessions, both active and inactive, that have been created in the sandbox.

```go
// List all PTY sessions
sessions, err := sandbox.Process.ListPtySessions(ctx)
if err != nil {
	log.Fatal(err)
}

for _, session := range sessions {
	fmt.Printf("Session ID: %s\n", session.Id)
	fmt.Printf("Active: %t\n", session.Active)
	fmt.Printf("Terminal Size: %dx%d\n", session.Cols, session.Rows)
	fmt.Println("---")
}
```

## Get PTY session info

Daytona provides methods to get information about a specific PTY session, allowing you to retrieve comprehensive information about a specific PTY session including its current state, configuration, and metadata.

```go
// Get details about a specific PTY session
session, err := sandbox.Process.GetPtySessionInfo(ctx, "my-session")
if err != nil {
	log.Fatal(err)
}

fmt.Printf("Session ID: %s\n", session.Id)
fmt.Printf("Active: %t\n", session.Active)
fmt.Printf("Working Directory: %s\n", session.Cwd)
fmt.Printf("Terminal Size: %dx%d\n", session.Cols, session.Rows)

if session.ProcessId != nil {
	fmt.Printf("Process ID: %d\n", *session.ProcessId)
}
```

## Kill PTY session

Daytona provides methods to kill a PTY session, allowing you to forcefully terminate a PTY session and cleans up all associated resources.

```go
// Kill a specific PTY session
err := sandbox.Process.KillPtySession(ctx, "my-session")
if err != nil {
	log.Fatal(err)
}

// Verify the session is no longer active
sessions, err := sandbox.Process.ListPtySessions(ctx)
if err != nil {
	log.Fatal(err)
}

for _, session := range sessions {
	fmt.Printf("PTY session: %s\n", session.Id)
}
```

## Resize PTY session

Daytona provides methods to resize a PTY session, allowing you to change the terminal dimensions of an active PTY session. This sends a SIGWINCH signal to the shell process, allowing terminal applications to adapt to the new size.

```go
// Resize a PTY session to a larger terminal
updatedInfo, err := sandbox.Process.ResizePtySession(ctx, "my-session", types.PtySize{
	Cols: 150,
	Rows: 40,
})
if err != nil {
	log.Fatal(err)
}

fmt.Printf("Terminal resized to %dx%d\n", updatedInfo.Cols, updatedInfo.Rows)

// You can also use the PtyHandle's Resize method
info, err := handle.Resize(ctx, 150, 40)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Terminal resized to %dx%d\n", info.Cols, info.Rows)
```

## Interactive commands

Daytona provides methods to handle interactive commands with PTY sessions, allowing you to handle interactive commands that require user input and can be resized during execution.

```go
// Create PTY session
handle, err := sandbox.Process.CreatePty(ctx, "interactive-session",
	options.WithCreatePtySize(types.PtySize{Cols: 300, Rows: 100}),
)
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

if err := handle.WaitForConnection(ctx); err != nil {
	log.Fatal(err)
}

// Handle output in a goroutine
go func() {
	for data := range handle.DataChan() {
		fmt.Print(string(data))
	}
}()

// Send interactive command
handle.SendInput([]byte(`printf "Are you accepting the terms and conditions? (y/n): " && read confirm && if [ "$confirm" = "y" ]; then echo "You accepted"; else echo "You did not accept"; fi` + "\n"))
time.Sleep(1 * time.Second)
handle.SendInput([]byte("y\n"))

// Resize terminal
info, err := handle.Resize(ctx, 210, 110)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("\nPTY session resized to %dx%d\n", info.Cols, info.Rows)

// Exit the session
handle.SendInput([]byte("exit\n"))

// Wait for completion
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Session completed with exit code: %d\n", *result.ExitCode)
```

## Long-running processes

Daytona provides methods to manage long-running processes with PTY sessions, allowing you to manage long-running processes that need to be monitored or terminated.

```go
// Create PTY session
handle, err := sandbox.Process.CreatePty(ctx, "long-running-session",
	options.WithCreatePtySize(types.PtySize{Cols: 120, Rows: 30}),
)
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

if err := handle.WaitForConnection(ctx); err != nil {
	log.Fatal(err)
}

// Handle output in a goroutine
go func() {
	for data := range handle.DataChan() {
		fmt.Print(string(data))
	}
}()

// Start a long-running process
handle.SendInput([]byte(`while true; do echo "Running... $(date)"; sleep 1; done` + "\n"))
time.Sleep(3 * time.Second) // Let it run for a bit

fmt.Println("Killing long-running process...")
if err := handle.Kill(ctx); err != nil {
	log.Fatal(err)
}

// Wait for termination
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("\nProcess terminated with exit code: %d\n", *result.ExitCode)
if result.Error != nil {
	fmt.Printf("Termination reason: %s\n", *result.Error)
}
```

## Resource management

Daytona provides methods to manage resource leaks with PTY sessions, allowing you to always clean up PTY sessions to prevent resource leaks.

```go
// Go: Use defer for cleanup
handle, err := sandbox.Process.CreatePty(ctx, "session",
	options.WithCreatePtySize(types.PtySize{Cols: 120, Rows: 30}),
)
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

// Do work...

// Or use Kill to terminate the process
defer handle.Kill(ctx)
```

## PtyHandle methods

Daytona provides methods to interact with PTY sessions, allowing you to send input, resize the terminal, wait for completion, and manage the WebSocket connection to a PTY session.

### Send input

Daytona provides methods to send input to a PTY session, allowing you to send input data (keystrokes or commands) to the PTY session.

```go
// Send a command
handle.SendInput([]byte("ls -la\n"))

// Send Ctrl+C
handle.SendInput([]byte{0x03})
```

### Wait for completion

Daytona provides methods to wait for a PTY process to exit and return the result, allowing you to wait for a PTY process to exit and return the result.

```go
// Wait for process to complete
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}

if result.ExitCode != nil && *result.ExitCode == 0 {
	fmt.Println("Process completed successfully")
} else {
	fmt.Printf("Process failed with code: %d\n", *result.ExitCode)
	if result.Error != nil {
		fmt.Printf("Error: %s\n", *result.Error)
	}
}
```

### Wait for connection

Daytona provides methods to wait for the WebSocket connection to be established before sending input, allowing you to wait for the WebSocket connection to be established before sending input.

```go
// Wait for connection to be established
if err := handle.WaitForConnection(ctx); err != nil {
	log.Fatal(err)
}

// Now safe to send input
handle.SendInput([]byte("echo 'Connected!'\n"))
```

### Kill PTY process

Daytona provides methods to kill a PTY process and terminate the session from the handle.

```go
// Kill a long-running process
if err := handle.Kill(ctx); err != nil {
	log.Fatal(err)
}

// Wait to confirm termination
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Process terminated with exit code: %d\n", *result.ExitCode)
```

### Resize from handle

Daytona provides methods to resize the PTY terminal dimensions directly from the handle.

```go
// Resize to 120x30
info, err := handle.Resize(ctx, 120, 30)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Resized to %dx%d\n", info.Cols, info.Rows)
```

### Disconnect

Daytona provides methods to disconnect from a PTY session and clean up resources without killing the process.

```go
// Always clean up when done using defer
handle, err := sandbox.Process.CreatePty(ctx, "session")
if err != nil {
	log.Fatal(err)
}
defer handle.Disconnect()

// ... use PTY session
```

### Check connection status

Daytona provides methods to check if a PTY session is still connected.

```go
if handle.IsConnected() {
	fmt.Println("PTY session is active")
}
```

### Exit code and error

Daytona provides methods to access the exit code and error message after a PTY process terminates.

```go
// Access via methods after process terminates
if exitCode := handle.ExitCode(); exitCode != nil {
	fmt.Printf("Exit code: %d\n", *exitCode)
}
if errMsg := handle.Error(); errMsg != nil {
	fmt.Printf("Error: %s\n", *errMsg)
}
```

### Iterate over output (Python)

Daytona provides methods to iterate over a PTY handle to receive output data.

```go
// Go uses a channel to receive output data
for data := range handle.DataChan() {
	fmt.Print(string(data))
}

// Or use as io.Reader
io.Copy(os.Stdout, handle)

fmt.Printf("Session ended with exit code: %d\n", *handle.ExitCode())
```

## Error handling

Daytona provides methods to monitor exit codes and handle errors appropriately with PTY sessions.

```go
// Go: Check exit codes
result, err := handle.Wait(ctx)
if err != nil {
	log.Fatal(err)
}

if result.ExitCode != nil && *result.ExitCode != 0 {
	fmt.Printf("Command failed: %d\n", *result.ExitCode)
	if result.Error != nil {
		fmt.Printf("Error: %s\n", *result.Error)
	}
}
```

## Troubleshooting

- **Connection issues**: verify sandbox status, network connectivity, and proper session IDs
- **Performance issues**: use appropriate terminal dimensions and efficient data handlers
- **Process management**: use explicit `kill()` calls and proper timeout handling for long-running processes

## See Also
- [Python SDK - pty](../python-sdk/pty.md)
- [TypeScript SDK - pty](../typescript-sdk/pty.md)
