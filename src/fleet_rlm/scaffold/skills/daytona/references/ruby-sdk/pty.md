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

```ruby
pty_size = Daytona::PtySize.new(rows: 30, cols: 120)
pty_handle = sandbox.process.create_pty_session(
  id: 'my-interactive-session',
  cwd: '/workspace',
  envs: { 'TERM' => 'xterm-256color' },
  pty_size: pty_size
)

# Use the PTY session
pty_handle.send_input("ls -la\n")
pty_handle.send_input("echo 'Hello, PTY!'\n")
pty_handle.send_input("exit\n")

# Handle output
pty_handle.each { |data| print data }

puts "PTY session completed with exit code: #{pty_handle.exit_code}"
```

## Connect to PTY session

Daytona provides methods to establish a connection to an existing PTY session, enabling interaction with a previously created terminal.

```ruby
# Connect to an existing PTY session
pty_handle = sandbox.process.connect_pty_session('my-session')
pty_handle.send_input("echo 'Hello World'\n")
pty_handle.send_input("exit\n")

# Handle output
pty_handle.each { |data| print data }

puts "Session exited with code: #{pty_handle.exit_code}"
```

## List PTY sessions

Daytona provides methods to list PTY sessions, allowing you to retrieve information about all PTY sessions, both active and inactive, that have been created in the sandbox.

```ruby
# List all PTY sessions
sessions = sandbox.process.list_pty_sessions

sessions.each do |session|
  puts "Session ID: #{session.id}"
  puts "Active: #{session.active}"
  puts "Terminal Size: #{session.cols}x#{session.rows}"
  puts '---'
end
```

## Get PTY session info

Daytona provides methods to get information about a specific PTY session, allowing you to retrieve comprehensive information about a specific PTY session including its current state, configuration, and metadata.

```ruby
# Get details about a specific PTY session
session_info = sandbox.process.get_pty_session_info('my-session')

puts "Session ID: #{session_info.id}"
puts "Active: #{session_info.active}"
puts "Working Directory: #{session_info.cwd}"
puts "Terminal Size: #{session_info.cols}x#{session_info.rows}"
```

## Kill PTY session

Daytona provides methods to kill a PTY session, allowing you to forcefully terminate a PTY session and cleans up all associated resources.

```ruby
# Delete a specific PTY session
sandbox.process.delete_pty_session('my-session')

# Verify the session no longer exists
sessions = sandbox.process.list_pty_sessions
sessions.each do |session|
  puts "PTY session: #{session.id}"
end
```

## Resize PTY session

Daytona provides methods to resize a PTY session, allowing you to change the terminal dimensions of an active PTY session. This sends a SIGWINCH signal to the shell process, allowing terminal applications to adapt to the new size.

```ruby
# Resize a PTY session to a larger terminal
pty_size = Daytona::PtySize.new(rows: 40, cols: 150)
session_info = sandbox.process.resize_pty_session('my-session', pty_size)

puts "Terminal resized to #{session_info.cols}x#{session_info.rows}"
```

## Interactive commands

Daytona provides methods to handle interactive commands with PTY sessions, allowing you to handle interactive commands that require user input and can be resized during execution.

```ruby
require 'daytona'

# Create PTY session
pty_handle = sandbox.process.create_pty_session(
  id: 'interactive-session',
  pty_size: Daytona::PtySize.new(cols: 300, rows: 100)
)

# Handle output in a separate thread
thread = Thread.new do
  pty_handle.each { |data| print data }
end

# Send interactive command
pty_handle.send_input('printf "Are you accepting the terms and conditions? (y/n): " && read confirm && if [ "$confirm" = "y" ]; then echo "You accepted"; else echo "You did not accept"; fi' + "\n")
sleep(1)
pty_handle.send_input("y\n")

# Resize terminal
pty_handle.resize(Daytona::PtySize.new(cols: 210, rows: 110))
puts "\nPTY session resized"

# Exit the session
pty_handle.send_input("exit\n")

# Wait for the thread to finish
thread.join

puts "Session completed with exit code: #{pty_handle.exit_code}"
```

## Long-running processes

Daytona provides methods to manage long-running processes with PTY sessions, allowing you to manage long-running processes that need to be monitored or terminated.

```ruby
require 'daytona'

# Create PTY session
pty_handle = sandbox.process.create_pty_session(
  id: 'long-running-session',
  pty_size: Daytona::PtySize.new(cols: 120, rows: 30)
)

# Handle output in a separate thread
thread = Thread.new do
  pty_handle.each { |data| print data }
end

# Start a long-running process
pty_handle.send_input("while true; do echo \"Running... $(date)\"; sleep 1; done\n")
sleep(3) # Let it run for a bit

puts "Killing long-running process..."
pty_handle.kill

thread.join

puts "\nProcess terminated with exit code: #{pty_handle.exit_code}"
puts "Termination reason: #{pty_handle.error}" if pty_handle.error
```

## Resource management

Daytona provides methods to manage resource leaks with PTY sessions, allowing you to always clean up PTY sessions to prevent resource leaks.

```ruby
# Ruby: Use begin/ensure
pty_handle = nil
begin
  pty_handle = sandbox.process.create_pty_session(
    id: 'session',
    pty_size: Daytona::PtySize.new(cols: 120, rows: 30)
  )
  # Do work...
ensure
  pty_handle&.kill
end
```

## PtyHandle methods

Daytona provides methods to interact with PTY sessions, allowing you to send input, resize the terminal, wait for completion, and manage the WebSocket connection to a PTY session.

### Send input

Daytona provides methods to send input to a PTY session, allowing you to send input data (keystrokes or commands) to the PTY session.

```ruby
# Send a command
pty_handle.send_input("ls -la\n")

# Send user input
pty_handle.send_input("y\n")
```

### Wait for completion

Daytona provides methods to wait for a PTY process to exit and return the result, allowing you to wait for a PTY process to exit and return the result.

```ruby
# Wait by iterating over output (blocks until PTY session ends)
pty_handle.each { |data| print data }

if pty_handle.exit_code == 0
  puts 'Process completed successfully'
else
  puts "Process failed with code: #{pty_handle.exit_code}"
  puts "Error: #{pty_handle.error}" if pty_handle.error
end
```

### Wait for connection

Daytona provides methods to wait for the WebSocket connection to be established before sending input, allowing you to wait for the WebSocket connection to be established before sending input.

```ruby
# Ruby handles connection internally during creation
# No explicit wait needed - can send input immediately after creation
pty_handle.send_input("echo 'Connected!'\n")
```

### Kill PTY process

Daytona provides methods to kill a PTY process and terminate the session from the handle.

```ruby
# Kill a long-running process
pty_handle.kill

puts "Process terminated with exit code: #{pty_handle.exit_code}"
```

### Resize from handle

Daytona provides methods to resize the PTY terminal dimensions directly from the handle.

```ruby
# Resize to 120x30
pty_handle.resize(Daytona::PtySize.new(cols: 120, rows: 30))
```

### Disconnect

Daytona provides methods to disconnect from a PTY session and clean up resources without killing the process.

```ruby
# Ruby: Use begin/ensure or kill the session
begin
  # ... use PTY session
ensure
  pty_handle.kill
end
```

### Check connection status

Daytona provides methods to check if a PTY session is still connected.

```ruby
# Ruby: Check by using session info
session_info = sandbox.process.get_pty_session_info('my-session')
puts 'PTY session is active' if session_info.active
```

### Exit code and error

Daytona provides methods to access the exit code and error message after a PTY process terminates.

```ruby
# Access after process terminates
puts "Exit code: #{pty_handle.exit_code}"
puts "Error: #{pty_handle.error}" if pty_handle.error
```

### Iterate over output (Python)

Daytona provides methods to iterate over a PTY handle to receive output data.

```ruby
# Iterate over PTY output
pty_handle.each do |data|
  print data
end

puts "Session ended with exit code: #{pty_handle.exit_code}"
```

## Error handling

Daytona provides methods to monitor exit codes and handle errors appropriately with PTY sessions.

```ruby
# Ruby: Check exit codes
# The handle blocks until the PTY session completes
pty_handle.each { |data| print data }

if pty_handle.exit_code != 0
  puts "Command failed: #{pty_handle.exit_code}"
  puts "Error: #{pty_handle.error}"
end
```

## Troubleshooting

- **Connection issues**: verify sandbox status, network connectivity, and proper session IDs
- **Performance issues**: use appropriate terminal dimensions and efficient data handlers
- **Process management**: use explicit `kill()` calls and proper timeout handling for long-running processes

## See Also
- [Python SDK - pty](../python-sdk/pty.md)
- [TypeScript SDK - pty](../typescript-sdk/pty.md)
- [Go SDK - pty](../go-sdk/pty.md)
