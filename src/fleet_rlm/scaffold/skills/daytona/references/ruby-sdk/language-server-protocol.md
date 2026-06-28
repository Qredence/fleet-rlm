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

```ruby
require 'daytona'

# Create Sandbox
daytona = Daytona::Daytona.new
sandbox = daytona.create

# Create LSP server for Python
lsp_server = sandbox.create_lsp_server(
  language_id: Daytona::LspServer::Language::PYTHON,
  path_to_project: 'workspace/project'
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

```ruby
lsp = sandbox.create_lsp_server(
  language_id: Daytona::LspServer::Language::PYTHON,
  path_to_project: 'workspace/project'
)
lsp.start  # Initialize the server
# Now ready for LSP operations
```

## Stop LSP servers

Daytona provides methods to stop LSP servers.

```ruby
# When done with LSP features
lsp.stop  # Clean up resources
```

## Code completions

Daytona provides methods to get code completions for a specific position in a file.

```ruby
completions = lsp_server.completions(
  path: 'workspace/project/main.py',
  position: { line: 10, character: 15 }
)
puts "Completions: #{completions}"
```

## File notifications

Daytona provides methods to notify the LSP server when files are opened or closed. This enables features like diagnostics and completion tracking for the specified files.

### Open file

Notifies the language server that a file has been opened for editing.

```ruby
# Notify server that a file is open
lsp_server.did_open('workspace/project/main.py')
```

### Close file

Notifies the language server that a file has been closed. This allows the server to clean up resources associated with that file.

```ruby
# Notify server that a file is closed
lsp_server.did_close('workspace/project/main.py')
```

## Document symbols

Daytona provides methods to retrieve symbols (functions, classes, variables, etc.) from a document.

```ruby
symbols = lsp_server.document_symbols('workspace/project/main.py')
symbols.each do |symbol|
  puts "Symbol: #{symbol.name}, Kind: #{symbol.kind}"
end
```

## Sandbox symbols

Daytona provides methods to search for symbols across all files in the sandbox.

```ruby
symbols = lsp_server.sandbox_symbols('MyClass')
symbols.each do |symbol|
  puts "Found: #{symbol.name} at #{symbol.location}"
end
```

## See Also
- [Python SDK - language-server-protocol](../python-sdk/language-server-protocol.md)
- [TypeScript SDK - language-server-protocol](../typescript-sdk/language-server-protocol.md)
- [Go SDK - language-server-protocol](../go-sdk/language-server-protocol.md)
