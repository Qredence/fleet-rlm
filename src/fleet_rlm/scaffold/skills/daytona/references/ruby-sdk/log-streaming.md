## Contents

- Stream logs with callbacks
- Retrieve all existing logs
- See Also




Log streaming allows you to access and process logs as they are being produced, while the process is still running. When executing long-running processes in a sandbox, you often want to access and process their logs in **real-time**.

Real-time streaming is especially useful for **debugging**, **monitoring**, or integrating with **observability tools**.

- [**Log streaming**](#stream-logs-with-callbacks): stream logs as they are being produced, while the process is still running.
- [**Fetching log snapshot**](#retrieve-all-existing-logs): retrieve all logs up to a certain point.

This guide covers how to use log streaming with callbacks and fetching log snapshots in both asynchronous and synchronous modes.
> **Note:**
> Starting with version `0.27.0`, you can retrieve session command logs in two distinct streams: **stdout** and **stderr**.

## Stream logs with callbacks

If your sandboxed process is part of a larger system and is expected to run for an extended period (or indefinitely),
you can process logs asynchronously **in the background**, while the rest of your system continues executing.

This is ideal for:

- Continuous monitoring
- Debugging long-running jobs
- Live log forwarding or visualizations

```ruby
require 'daytona'

daytona = Daytona::Daytona.new
sandbox = daytona.create

session_id = 'streaming-session'
sandbox.process.create_session(session_id)

command = sandbox.process.execute_session_command(
  session_id,
  Daytona::SessionExecuteRequest.new(
    command: 'for i in {1..5}; do echo "Step $i"; echo "Error $i" >&2; sleep 1; done',
    var_async: true
  )
)

# Stream logs using a thread
log_thread = Thread.new do
  sandbox.process.get_session_command_logs_stream(
    session_id,
    command.cmd_id,
    on_stdout: ->(stdout) { puts "[STDOUT]: #{stdout}" },
    on_stderr: ->(stderr) { puts "[STDERR]: #{stderr}" }
  )
end

puts 'Continuing execution while logs are streaming...'
sleep(3)
puts 'Other operations completed!'

# Wait for the logs to complete
log_thread.join

daytona.delete(sandbox)
```

## Retrieve all existing logs

If the command has a predictable duration, or if you don't need to run it in the background but want to
periodically check all existing logs, you can use the following example to get the logs up to the current point in time.

```ruby
require 'daytona'

daytona = Daytona::Daytona.new
sandbox = daytona.create
session_id = 'exec-session-1'
sandbox.process.create_session(session_id)

# Execute a blocking command and wait for the result
command = sandbox.process.execute_session_command(
  session_id,
  Daytona::SessionExecuteRequest.new(
    command: 'echo "Hello from stdout" && echo "Hello from stderr" >&2'
  )
)
puts "[STDOUT]: #{command.stdout}"
puts "[STDERR]: #{command.stderr}"
puts "[OUTPUT]: #{command.output}"

# Or execute command in the background and get the logs later
command = sandbox.process.execute_session_command(
  session_id,
  Daytona::SessionExecuteRequest.new(
    command: 'while true; do if (( RANDOM % 2 )); then echo "All good at $(date)"; else echo "Oops, an error at $(date)" >&2; fi; sleep 1; done',
    var_async: true
  )
)
sleep(5)
# Get the logs up to the current point in time
logs = sandbox.process.get_session_command_logs(session_id, command.cmd_id)
puts "[STDOUT]: #{logs.stdout}"
puts "[STDERR]: #{logs.stderr}"
puts "[OUTPUT]: #{logs.output}"

daytona.delete(sandbox)
```

## See Also
- [Python SDK - log-streaming](../python-sdk/log-streaming.md)
- [TypeScript SDK - log-streaming](../typescript-sdk/log-streaming.md)
- [Go SDK - log-streaming](../go-sdk/log-streaming.md)
