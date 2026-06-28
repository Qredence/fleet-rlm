## Contents

- Create LSP servers
- Start LSP servers
- Stop LSP servers
- Code completions
- File notifications
- Document symbols
- Sandbox symbols
- See Also




Daytona provides Language Server Protocol (LSP) support through sandbox instances. This enables advanced language features like code completion, diagnostics, and more.

## Create LSP servers

Daytona provides methods to create LSP servers. The `path_to_project` argument is relative to the current sandbox working directory when no leading `/` is used. The working directory is specified by WORKDIR when it is present in the Dockerfile, and otherwise falls back to the user's home directory.

```go
// Create sandbox
client, err := daytona.NewClient()
if err != nil {
	log.Fatal(err)
}

ctx := context.Background()
sandbox, err := client.Create(ctx, nil)
if err != nil {
	log.Fatal(err)
}

// Create LSP server for Python
lsp := sandbox.CreateLspServer(types.LspLanguagePython, "workspace/project")
```

### Supported languages

The supported languages for creating LSP servers with Daytona are defined by the `LspLanguageId` enum:

| Enum Value                     | Description                            |
| ------------------------------ | -------------------------------------- |
| **`LspLanguageId.PYTHON`**     | Python language server                 |
| **`LspLanguageId.TYPESCRIPT`** | TypeScript/JavaScript language server  |

## Start LSP servers

Daytona provides methods to start LSP servers.

```go
lsp := sandbox.CreateLspServer(types.LspLanguagePython, "workspace/project")
err := lsp.Start(ctx)  // Initialize the server
if err != nil {
	log.Fatal(err)
}
// Now ready for LSP operations
```

## Stop LSP servers

Daytona provides methods to stop LSP servers.

```go
// When done with LSP features
err := lsp.Stop(ctx)  // Clean up resources
if err != nil {
	log.Fatal(err)
}
```

## Code completions

Daytona provides methods to get code completions for a specific position in a file.

```go
completions, err := lsp.Completions(ctx, "workspace/project/main.py",
	types.Position{Line: 10, Character: 15},
)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Completions: %v\n", completions)
```

## File notifications

Daytona provides methods to notify the LSP server when files are opened or closed. This enables features like diagnostics and completion tracking for the specified files.

### Open file

Notifies the language server that a file has been opened for editing.

```go
// Notify server that a file is open
err := lsp.DidOpen(ctx, "workspace/project/main.py")
if err != nil {
	log.Fatal(err)
}
```

### Close file

Notifies the language server that a file has been closed. This allows the server to clean up resources associated with that file.

```go
// Notify server that a file is closed
err := lsp.DidClose(ctx, "workspace/project/main.py")
if err != nil {
	log.Fatal(err)
}
```

## Document symbols

Daytona provides methods to retrieve symbols (functions, classes, variables, etc.) from a document.

```go
symbols, err := lsp.DocumentSymbols(ctx, "workspace/project/main.py")
if err != nil {
	log.Fatal(err)
}
for _, symbol := range symbols {
	fmt.Printf("Symbol: %v\n", symbol)
}
```

## Sandbox symbols

Daytona provides methods to search for symbols across all files in the sandbox.

```go
symbols, err := lsp.SandboxSymbols(ctx, "MyClass")
if err != nil {
	log.Fatal(err)
}
for _, symbol := range symbols {
	fmt.Printf("Found: %v\n", symbol)
}
```

## See Also
- [Python SDK - language-server-protocol](../python-sdk/language-server-protocol.md)
- [TypeScript SDK - language-server-protocol](../typescript-sdk/language-server-protocol.md)
