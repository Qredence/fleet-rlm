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

```typescript
import { Daytona, LspLanguageId } from '@daytona/sdk'

// Create sandbox
const daytona = new Daytona()
const sandbox = await daytona.create({
  language: 'typescript',
})

// Create LSP server for TypeScript
const lspServer = await sandbox.createLspServer(
  LspLanguageId.TYPESCRIPT,
  'workspace/project'
)
```

### Supported languages

The supported languages for creating LSP servers with Daytona are defined by the `LspLanguageId` enum:

| Enum Value                     | Description                            |
| ------------------------------ | -------------------------------------- |
| **`LspLanguageId.PYTHON`**     | Python language server                 |
| **`LspLanguageId.TYPESCRIPT`** | TypeScript/JavaScript language server  |

## Start LSP servers

Daytona provides methods to start LSP servers.

```typescript
const lsp = await sandbox.createLspServer('typescript', 'workspace/project')
await lsp.start() // Initialize the server
// Now ready for LSP operations
```

## Stop LSP servers

Daytona provides methods to stop LSP servers.

```typescript
// When done with LSP features
await lsp.stop() // Clean up resources
```

## Code completions

Daytona provides methods to get code completions for a specific position in a file.

```typescript
const completions = await lspServer.completions('workspace/project/main.ts', {
  line: 10,
  character: 15,
})
console.log('Completions:', completions)
```

## File notifications

Daytona provides methods to notify the LSP server when files are opened or closed. This enables features like diagnostics and completion tracking for the specified files.

### Open file

Notifies the language server that a file has been opened for editing.

```typescript
// Notify server that a file is open
await lspServer.didOpen('workspace/project/main.ts')
```

### Close file

Notifies the language server that a file has been closed. This allows the server to clean up resources associated with that file.

```typescript
// Notify server that a file is closed
await lspServer.didClose('workspace/project/main.ts')
```

## Document symbols

Daytona provides methods to retrieve symbols (functions, classes, variables, etc.) from a document.

```typescript
const symbols = await lspServer.documentSymbols('workspace/project/main.ts')
symbols.forEach((symbol) => {
  console.log(`Symbol: ${symbol.name}, Kind: ${symbol.kind}`)
})
```

## Sandbox symbols

Daytona provides methods to search for symbols across all files in the sandbox.

```typescript
const symbols = await lspServer.sandboxSymbols('MyClass')
symbols.forEach((symbol) => {
  console.log(`Found: ${symbol.name} at ${symbol.location}`)
})
```

## See Also
- [Python SDK - language-server-protocol](../python-sdk/language-server-protocol.md)
