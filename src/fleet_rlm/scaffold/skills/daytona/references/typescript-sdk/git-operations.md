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

```typescript
// Basic clone
await sandbox.git.clone(
    "https://github.com/user/repo.git",
    "workspace/repo"
);

// Clone with authentication
await sandbox.git.clone(
    "https://github.com/user/repo.git",
    "workspace/repo",
    undefined,
    undefined,
    "git",
    "personal_access_token"
);

// Clone specific branch
await sandbox.git.clone(
    "https://github.com/user/repo.git",
    "workspace/repo",
    "develop"
);

// Clone from a self-signed internal Git server (insecure)
await sandbox.git.clone(
    "https://internal-git.example.com/org/repo.git",
    "workspace/repo",
    undefined,
    undefined,
    undefined,
    undefined,
    true
);
```

### Get repository status

Daytona provides methods to check the status of Git repositories in sandboxes. You can get the current branch, modified files, number of commits ahead and behind main branch.

```typescript
// Get repository status
const status = await sandbox.git.status("workspace/repo");
console.log(`Current branch: ${status.currentBranch}`);
console.log(`Commits ahead: ${status.ahead}`);
console.log(`Commits behind: ${status.behind}`);
status.fileStatus.forEach(file => {
    console.log(`File: ${file.name}`);
});

// List branches
const response = await sandbox.git.branches("workspace/repo");
response.branches.forEach(branch => {
    console.log(`Branch: ${branch}`);
});
```

## Branch operations

Daytona provides methods to manage branches in Git repositories. You can create, switch, and delete branches.

### Create branches

Daytona provides methods to create branches in Git repositories. The following snippet creates a new branch called `new-feature`.

```typescript
// Create new branch
await git.createBranch('workspace/repo', 'new-feature');
```

### Checkout branches

Daytona provides methods to checkout branches in Git repositories. The following snippet checks out the branch called `feature-branch`.

```typescript
// Checkout a branch
await git.checkoutBranch('workspace/repo', 'feature-branch');
```

### Delete branches

Daytona provides methods to delete branches in Git repositories. The following snippet deletes the branch called `old-feature`.

```typescript
// Delete a branch
await git.deleteBranch('workspace/repo', 'old-feature');
```

## Stage changes

Daytona provides methods to stage changes in Git repositories. You can stage specific files, all changes, and commit with a message. The following snippet stages the file `file.txt` and the `src` directory.

```typescript
// Stage a single file
await git.add('workspace/repo', ['file.txt']);
// Stage whole repository
await git.add('workspace/repo', ['.']);
```

## Commit changes

Daytona provides methods to commit changes in Git repositories. You can commit with a message, author, and email. The following snippet commits the changes with the message `Update documentation` and the author `John Doe` and email `john@example.com`.

```typescript
// Stage and commit changes
await git.add('workspace/repo', ['README.md']);
await git.commit(
  'workspace/repo',
  'Update documentation',
  'John Doe',
  'john@example.com',
  true
);
```

## Remote operations

Daytona provides methods to work with remote repositories in Git. You can push and pull changes from remote repositories.

### Push changes

Daytona provides methods to push changes to remote repositories. The following snippet pushes the changes to a public repository.

```typescript
// Push to a public repository
await git.push('workspace/repo');

// Push to a private repository
await git.push(
  'workspace/repo',
  'user',
  'token'
);
```

### Pull changes

Daytona provides methods to pull changes from remote repositories. The following snippet pulls the changes from a public repository.

```typescript
// Pull from a public repository
await git.pull('workspace/repo');

// Pull from a private repository
await git.pull(
  'workspace/repo',
  'user',
  'token'
);
```

## See Also
- [Python SDK - git-operations](../python-sdk/git-operations.md)
