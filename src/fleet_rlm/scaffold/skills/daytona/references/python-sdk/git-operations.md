## Contents

- Basic operations
- Branch operations
- Stage changes
- Commit changes
- Remote operations




Daytona provides built-in Git support through the `git` module in sandboxes.

## Basic operations

Daytona provides methods to clone, check status, and manage Git repositories in sandboxes.

Similar to [file system operations](./file-system-operations.md), the starting cloning directory is the current sandbox working directory. It uses the WORKDIR specified in the Dockerfile if present, or falls back to the user's home directory if not - e.g. `workspace/repo` implies `/my-work-dir/workspace/repo`, but you are free to provide an absolute `workDir` path as well (by starting the path with `/`).

### Clone repositories

Daytona provides methods to clone Git repositories into sandboxes. You can clone public or private repositories, specific branches, and authenticate using personal access tokens.

Clones verify the remote's TLS certificate by default. For clones against internal Git servers that use self-signed or private-CA certificates, pass `insecure_skip_tls=true` (`insecureSkipTls: true` in TypeScript / Java). The bypass is per-request and disables TLS verification for that clone only; credentials, if supplied, are transmitted over an unverified TLS connection and are exposed to any MITM on the route. Prefer adding the server's CA to the sandbox base image's trust store when possible.

```python
# Basic clone
sandbox.git.clone(
    url="https://github.com/user/repo.git",
    path="workspace/repo"
)

# Clone with authentication
sandbox.git.clone(
    url="https://github.com/user/repo.git",
    path="workspace/repo",
    username="git",
    password="personal_access_token"
)

# Clone specific branch
sandbox.git.clone(
    url="https://github.com/user/repo.git",
    path="workspace/repo",
    branch="develop"
)

# Clone from a self-signed internal Git server (insecure)
sandbox.git.clone(
    url="https://internal-git.example.com/org/repo.git",
    path="workspace/repo",
    insecure_skip_tls=True
)
```

### Get repository status

Daytona provides methods to check the status of Git repositories in sandboxes. You can get the current branch, modified files, number of commits ahead and behind main branch.

```python
# Get repository status
status = sandbox.git.status("workspace/repo")
print(f"Current branch: {status.current_branch}")
print(f"Commits ahead: {status.ahead}")
print(f"Commits behind: {status.behind}")
for file in status.file_status:
    print(f"File: {file.name}")

# List branches
response = sandbox.git.branches("workspace/repo")
for branch in response.branches:
    print(f"Branch: {branch}")
```

## Branch operations

Daytona provides methods to manage branches in Git repositories. You can create, switch, and delete branches.

### Create branches

Daytona provides methods to create branches in Git repositories. The following snippet creates a new branch called `new-feature`.

```python
# Create a new branch
sandbox.git.create_branch("workspace/repo", "new-feature")
```

### Checkout branches

Daytona provides methods to checkout branches in Git repositories. The following snippet checks out the branch called `feature-branch`.

```python
# Checkout a branch
sandbox.git.checkout_branch("workspace/repo", "feature-branch")
```

### Delete branches

Daytona provides methods to delete branches in Git repositories. The following snippet deletes the branch called `old-feature`.

```python
# Delete a branch
sandbox.git.delete_branch("workspace/repo", "old-feature")
```

## Stage changes

Daytona provides methods to stage changes in Git repositories. You can stage specific files, all changes, and commit with a message. The following snippet stages the file `file.txt` and the `src` directory.

```python
# Stage a single file
sandbox.git.add("workspace/repo", ["file.txt"])

# Stage multiple files
sandbox.git.add("workspace/repo", [
    "src/main.py",
    "tests/test_main.py",
    "README.md"
])
```

## Commit changes

Daytona provides methods to commit changes in Git repositories. You can commit with a message, author, and email. The following snippet commits the changes with the message `Update documentation` and the author `John Doe` and email `john@example.com`.

```python
# Stage and commit changes
sandbox.git.add("workspace/repo", ["README.md"])
sandbox.git.commit(
    path="workspace/repo",
    message="Update documentation",
    author="John Doe",
    email="john@example.com",
    allow_empty=True
)
```

## Remote operations

Daytona provides methods to work with remote repositories in Git. You can push and pull changes from remote repositories.

### Push changes

Daytona provides methods to push changes to remote repositories. The following snippet pushes the changes to a public repository.

```python
# Push without authentication (for public repos or SSH)
sandbox.git.push("workspace/repo")

# Push with authentication
sandbox.git.push(
    path="workspace/repo",
    username="user",
    password="github_token"
)
```

### Pull changes

Daytona provides methods to pull changes from remote repositories. The following snippet pulls the changes from a public repository.

```python
# Pull without authentication
sandbox.git.pull("workspace/repo")

# Pull with authentication
sandbox.git.pull(
    path="workspace/repo",
    username="user",
    password="github_token"
)
```
