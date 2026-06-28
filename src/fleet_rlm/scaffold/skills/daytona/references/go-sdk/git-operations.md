## Contents

- Basic operations
- Branch operations
- Stage changes
- Commit changes
- Remote operations
- See Also




Daytona provides built-in Git support through the `git` module in sandboxes.

## Basic operations

Daytona provides methods to clone, check status, and manage Git repositories in sandboxes.

Similar to [file system operations](./file-system-operations.md), the starting cloning directory is the current sandbox working directory. It uses the WORKDIR specified in the Dockerfile if present, or falls back to the user's home directory if not - e.g. `workspace/repo` implies `/my-work-dir/workspace/repo`, but you are free to provide an absolute `workDir` path as well (by starting the path with `/`).

### Clone repositories

Daytona provides methods to clone Git repositories into sandboxes. You can clone public or private repositories, specific branches, and authenticate using personal access tokens.

Clones verify the remote's TLS certificate by default. For clones against internal Git servers that use self-signed or private-CA certificates, pass `insecure_skip_tls=true` (`insecureSkipTls: true` in TypeScript / Java). The bypass is per-request and disables TLS verification for that clone only; credentials, if supplied, are transmitted over an unverified TLS connection and are exposed to any MITM on the route. Prefer adding the server's CA to the sandbox base image's trust store when possible.

```go
// Basic clone
err := sandbox.Git.Clone(ctx, "https://github.com/user/repo.git", "workspace/repo")
if err != nil {
	log.Fatal(err)
}

// Clone with authentication
err = sandbox.Git.Clone(ctx, "https://github.com/user/repo.git", "workspace/repo",
	options.WithUsername("git"),
	options.WithPassword("personal_access_token"),
)
if err != nil {
	log.Fatal(err)
}

// Clone specific branch
err = sandbox.Git.Clone(ctx, "https://github.com/user/repo.git", "workspace/repo",
	options.WithBranch("develop"),
)
if err != nil {
	log.Fatal(err)
}

// Clone from a self-signed internal Git server (insecure)
err = sandbox.Git.Clone(ctx, "https://internal-git.example.com/org/repo.git", "workspace/repo",
	options.WithInsecureSkipTLS(true),
)
if err != nil {
	log.Fatal(err)
}
```

### Get repository status

Daytona provides methods to check the status of Git repositories in sandboxes. You can get the current branch, modified files, number of commits ahead and behind main branch.

```go
// Get repository status
status, err := sandbox.Git.Status(ctx, "workspace/repo")
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Current branch: %s\n", status.CurrentBranch)
fmt.Printf("Commits ahead: %d\n", status.Ahead)
fmt.Printf("Commits behind: %d\n", status.Behind)
for _, file := range status.FileStatus {
	fmt.Printf("File: %s\n", file.Path)
}

// List branches
branches, err := sandbox.Git.Branches(ctx, "workspace/repo")
if err != nil {
	log.Fatal(err)
}
for _, branch := range branches {
	fmt.Printf("Branch: %s\n", branch)
}
```

## Branch operations

Daytona provides methods to manage branches in Git repositories. You can create, switch, and delete branches.

### Create branches

Daytona provides methods to create branches in Git repositories. The following snippet creates a new branch called `new-feature`.

```go
// Create a new branch
err := sandbox.Git.CreateBranch(ctx, "workspace/repo", "new-feature")
if err != nil {
	log.Fatal(err)
}
```

### Checkout branches

Daytona provides methods to checkout branches in Git repositories. The following snippet checks out the branch called `feature-branch`.

```go
// Checkout a branch
err := sandbox.Git.Checkout(ctx, "workspace/repo", "feature-branch")
if err != nil {
	log.Fatal(err)
}
```

### Delete branches

Daytona provides methods to delete branches in Git repositories. The following snippet deletes the branch called `old-feature`.

```go
// Delete a branch
err := sandbox.Git.DeleteBranch(ctx, "workspace/repo", "old-feature")
if err != nil {
	log.Fatal(err)
}
```

## Stage changes

Daytona provides methods to stage changes in Git repositories. You can stage specific files, all changes, and commit with a message. The following snippet stages the file `file.txt` and the `src` directory.

```go
// Stage a single file
err := sandbox.Git.Add(ctx, "workspace/repo", []string{"file.txt"})
if err != nil {
	log.Fatal(err)
}

// Stage multiple files
err = sandbox.Git.Add(ctx, "workspace/repo", []string{
	"src/main.py",
	"tests/test_main.py",
	"README.md",
})
if err != nil {
	log.Fatal(err)
}

// Stage whole repository
err = sandbox.Git.Add(ctx, "workspace/repo", []string{"."})
if err != nil {
	log.Fatal(err)
}
```

## Commit changes

Daytona provides methods to commit changes in Git repositories. You can commit with a message, author, and email. The following snippet commits the changes with the message `Update documentation` and the author `John Doe` and email `john@example.com`.

```go
// Stage and commit changes
err := sandbox.Git.Add(ctx, "workspace/repo", []string{"README.md"})
if err != nil {
	log.Fatal(err)
}

response, err := sandbox.Git.Commit(ctx, "workspace/repo",
	"Update documentation",
	"John Doe",
	"john@example.com",
	options.WithAllowEmpty(true),
)
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Commit SHA: %s\n", response.SHA)
```

## Remote operations

Daytona provides methods to work with remote repositories in Git. You can push and pull changes from remote repositories.

### Push changes

Daytona provides methods to push changes to remote repositories. The following snippet pushes the changes to a public repository.

```go
// Push without authentication (for public repos or SSH)
err := sandbox.Git.Push(ctx, "workspace/repo")
if err != nil {
	log.Fatal(err)
}

// Push with authentication
err = sandbox.Git.Push(ctx, "workspace/repo",
	options.WithPushUsername("user"),
	options.WithPushPassword("github_token"),
)
if err != nil {
	log.Fatal(err)
}
```

### Pull changes

Daytona provides methods to pull changes from remote repositories. The following snippet pulls the changes from a public repository.

```go
// Pull without authentication
err := sandbox.Git.Pull(ctx, "workspace/repo")
if err != nil {
	log.Fatal(err)
}

// Pull with authentication
err = sandbox.Git.Pull(ctx, "workspace/repo",
	options.WithPullUsername("user"),
	options.WithPullPassword("github_token"),
)
if err != nil {
	log.Fatal(err)
}
```

## See Also
- [Python SDK - git-operations](../python-sdk/git-operations.md)
- [TypeScript SDK - git-operations](../typescript-sdk/git-operations.md)
