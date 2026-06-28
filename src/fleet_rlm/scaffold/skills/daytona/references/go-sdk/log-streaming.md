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

```go
package main

import (
	"context"
	"fmt"
	"log"
	"os"

	"github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
)

func main() {
	client, _ := daytona.NewClient()
	ctx := context.Background()
	sandbox, _ := client.Create(ctx, nil)

	sessionID := "streaming-session"
	sandbox.Process.CreateSession(ctx, sessionID)

	// Execute async command that outputs to stdout and stderr
	cmd := `for i in 1 2 3 4 5; do echo "Step $i"; echo "Error $i" >&2; sleep 1; done`
	cmdResult, _ := sandbox.Process.ExecuteSessionCommand(ctx, sessionID, cmd, true)
	cmdID, _ := cmdResult["id"].(string)

	// Create channels for stdout and stderr
	stdout := make(chan string, 100)
	stderr := make(chan string, 100)

	// Stream logs in a goroutine
	go func() {
		err := sandbox.Process.GetSessionCommandLogsStream(ctx, sessionID, cmdID, stdout, stderr)
		if err != nil {
			log.Printf("Stream error: %v", err)
		}
	}()

	fmt.Println("Continuing execution while logs are streaming...")

	// Read from channels until both are closed
	stdoutOpen, stderrOpen := true, true
	for stdoutOpen || stderrOpen {
		select {
		case chunk, ok := <-stdout:
			if !ok {
				stdoutOpen = false
			} else {
				fmt.Fprintf(os.Stdout, "[STDOUT]: %s", chunk)
			}
		case chunk, ok := <-stderr:
			if !ok {
				stderrOpen = false
			} else {
				fmt.Fprintf(os.Stderr, "[STDERR]: %s", chunk)
			}
		}
	}

	fmt.Println("Streaming completed!")
	sandbox.Delete(ctx)
}
```

## Retrieve all existing logs

If the command has a predictable duration, or if you don't need to run it in the background but want to
periodically check all existing logs, you can use the following example to get the logs up to the current point in time.

```go
package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
)

func main() {
	client, _ := daytona.NewClient()
	ctx := context.Background()
	sandbox, _ := client.Create(ctx, nil)

	sessionID := "exec-session-1"
	sandbox.Process.CreateSession(ctx, sessionID)

	// Execute a blocking command and wait for the result
	cmd1, _ := sandbox.Process.ExecuteSessionCommand(ctx, sessionID,
		`echo "Hello from stdout" && echo "Hello from stderr" >&2`, false)
	if stdout, ok := cmd1["stdout"].(string); ok {
		fmt.Printf("[STDOUT]: %s\n", stdout)
	}
	if stderr, ok := cmd1["stderr"].(string); ok {
		fmt.Printf("[STDERR]: %s\n", stderr)
	}

	// Or execute command in the background and get the logs later
	cmd := `counter=1; while (( counter <= 5 )); do echo "Count: $counter"; ((counter++)); sleep 1; done`
	cmdResult, _ := sandbox.Process.ExecuteSessionCommand(ctx, sessionID, cmd, true)
	cmdID, _ := cmdResult["id"].(string)

	time.Sleep(5 * time.Second)

	// Get the logs up to the current point in time
	logs, err := sandbox.Process.GetSessionCommandLogs(ctx, sessionID, cmdID)
	if err != nil {
		log.Fatalf("Failed to get logs: %v", err)
	}
	if logContent, ok := logs["logs"].(string); ok {
		fmt.Printf("[LOGS]: %s\n", logContent)
	}

	sandbox.Delete(ctx)
}
```

## See Also
- [Python SDK - log-streaming](../python-sdk/log-streaming.md)
- [TypeScript SDK - log-streaming](../typescript-sdk/log-streaming.md)
