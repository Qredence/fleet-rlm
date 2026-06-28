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

```ruby
# Basic clone
sandbox.git.clone(
  url: 'https://github.com/user/repo.git',
  path: 'workspace/repo'
)

# Clone with authentication
sandbox.git.clone(
  url: 'https://github.com/user/repo.git',
  path: 'workspace/repo',
  username: 'git',
  password: 'personal_access_token'
)

# Clone specific branch
sandbox.git.clone(
  url: 'https://github.com/user/repo.git',
  path: 'workspace/repo',
  branch: 'develop'
)

# Clone from a self-signed internal Git server (insecure)
sandbox.git.clone(
  url: 'https://internal-git.example.com/org/repo.git',
  path: 'workspace/repo',
  insecure_skip_tls: true
)
```

### Get repository status

Daytona provides methods to check the status of Git repositories in sandboxes. You can get the current branch, modified files, number of commits ahead and behind main branch.

```ruby
# Get repository status
status = sandbox.git.status('workspace/repo')
puts "Current branch: #{status.current_branch}"
puts "Commits ahead: #{status.ahead}"
puts "Commits behind: #{status.behind}"
status.file_status.each do |file|
  puts "File: #{file.name}"
end

# List branches
response = sandbox.git.branches('workspace/repo')
response.branches.each do |branch|
  puts "Branch: #{branch}"
end
```

## Branch operations

Daytona provides methods to manage branches in Git repositories. You can create, switch, and delete branches.

### Create branches

Daytona provides methods to create branches in Git repositories. The following snippet creates a new branch called `new-feature`.

```ruby
# Create a new branch
sandbox.git.create_branch('workspace/repo', 'new-feature')
```

### Checkout branches

Daytona provides methods to checkout branches in Git repositories. The following snippet checks out the branch called `feature-branch`.

```ruby
# Checkout a branch
sandbox.git.checkout_branch('workspace/repo', 'feature-branch')
```

### Delete branches

Daytona provides methods to delete branches in Git repositories. The following snippet deletes the branch called `old-feature`.

```ruby
# Delete a branch
sandbox.git.delete_branch('workspace/repo', 'old-feature')
```

## Stage changes

Daytona provides methods to stage changes in Git repositories. You can stage specific files, all changes, and commit with a message. The following snippet stages the file `file.txt` and the `src` directory.

```ruby
# Stage a single file
sandbox.git.add('workspace/repo', ['file.txt'])
```

## Commit changes

Daytona provides methods to commit changes in Git repositories. You can commit with a message, author, and email. The following snippet commits the changes with the message `Update documentation` and the author `John Doe` and email `john@example.com`.

```ruby
# Stage and commit changes
sandbox.git.add('workspace/repo', ['README.md'])
sandbox.git.commit('workspace/repo', 'Update documentation', 'John Doe', 'john@example.com', true)
```

## Remote operations

Daytona provides methods to work with remote repositories in Git. You can push and pull changes from remote repositories.

### Push changes

Daytona provides methods to push changes to remote repositories. The following snippet pushes the changes to a public repository.

```ruby
# Push changes
sandbox.git.push('workspace/repo')
```

### Pull changes

Daytona provides methods to pull changes from remote repositories. The following snippet pulls the changes from a public repository.

```ruby
# Pull changes
sandbox.git.pull('workspace/repo')
```

## See Also
- [Python SDK - git-operations](../python-sdk/git-operations.md)
- [TypeScript SDK - git-operations](../typescript-sdk/git-operations.md)
- [Go SDK - git-operations](../go-sdk/git-operations.md)
