

# types


## Contents

- Index
- Constants
- type Chart
- type CodeLanguage
- type CodeRunParams
- type CreateSnapshotParams
- type DaytonaConfig
- type ExecuteResponse
- type ExecutionArtifacts
- type ExecutionError
- type ExecutionResult
- type ExperimentalConfig
- type FileDownloadRequest
- type FileDownloadResponse
- type FileInfo
- type FileStatus
- type FileUpload
- type GitCommitResponse
- type GitStatus
- type GpuType
- type ImageParams
- type LspLanguageID
- type OutputMessage
- type PaginatedSnapshots
- type Position
- type PreviewLink
- type PtyResult
- type PtySessionInfo
- type PtySize
- type Resources
- type SandboxBaseParams
- type SandboxClass
- type ScreenshotOptions
- type ScreenshotRegion
- type ScreenshotResponse
- type SignedPreviewLink
- type Snapshot
- type SnapshotParams
- type Volume
- type VolumeMount

```go
import "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
```

## Index

- [Constants](https://www.daytona.io/docs/en<#constants>)
- [type Chart](https://www.daytona.io/docs/en<#Chart>)
- [type CodeLanguage](https://www.daytona.io/docs/en<#CodeLanguage>)
- [type CodeRunParams](https://www.daytona.io/docs/en<#CodeRunParams>)
- [type CreateSnapshotParams](https://www.daytona.io/docs/en<#CreateSnapshotParams>)
- [type DaytonaConfig](https://www.daytona.io/docs/en<#DaytonaConfig>)
- [type ExecuteResponse](https://www.daytona.io/docs/en<#ExecuteResponse>)
- [type ExecutionArtifacts](https://www.daytona.io/docs/en<#ExecutionArtifacts>)
- [type ExecutionError](https://www.daytona.io/docs/en<#ExecutionError>)
- [type ExecutionResult](https://www.daytona.io/docs/en<#ExecutionResult>)
- [type ExperimentalConfig](https://www.daytona.io/docs/en<#ExperimentalConfig>)
- [type FileDownloadRequest](https://www.daytona.io/docs/en<#FileDownloadRequest>)
- [type FileDownloadResponse](https://www.daytona.io/docs/en<#FileDownloadResponse>)
- [type FileInfo](https://www.daytona.io/docs/en<#FileInfo>)
- [type FileStatus](https://www.daytona.io/docs/en<#FileStatus>)
- [type FileUpload](https://www.daytona.io/docs/en<#FileUpload>)
- [type GitCommitResponse](https://www.daytona.io/docs/en<#GitCommitResponse>)
- [type GitStatus](https://www.daytona.io/docs/en<#GitStatus>)
- [type GpuType](https://www.daytona.io/docs/en<#GpuType>)
- [type ImageParams](https://www.daytona.io/docs/en<#ImageParams>)
- [type LspLanguageID](https://www.daytona.io/docs/en<#LspLanguageID>)
- [type OutputMessage](https://www.daytona.io/docs/en<#OutputMessage>)
- [type PaginatedSnapshots](https://www.daytona.io/docs/en<#PaginatedSnapshots>)
- [type Position](https://www.daytona.io/docs/en<#Position>)
- [type PreviewLink](https://www.daytona.io/docs/en<#PreviewLink>)
- [type PtyResult](https://www.daytona.io/docs/en<#PtyResult>)
- [type PtySessionInfo](https://www.daytona.io/docs/en<#PtySessionInfo>)
- [type PtySize](https://www.daytona.io/docs/en<#PtySize>)
- [type Resources](https://www.daytona.io/docs/en<#Resources>)
- [type SandboxBaseParams](https://www.daytona.io/docs/en<#SandboxBaseParams>)
- [type SandboxClass](https://www.daytona.io/docs/en<#SandboxClass>)
- [type ScreenshotOptions](https://www.daytona.io/docs/en<#ScreenshotOptions>)
- [type ScreenshotRegion](https://www.daytona.io/docs/en<#ScreenshotRegion>)
- [type ScreenshotResponse](https://www.daytona.io/docs/en<#ScreenshotResponse>)
- [type SignedPreviewLink](https://www.daytona.io/docs/en<#SignedPreviewLink>)
- [type Snapshot](https://www.daytona.io/docs/en<#Snapshot>)
- [type SnapshotParams](https://www.daytona.io/docs/en<#SnapshotParams>)
- [type Volume](https://www.daytona.io/docs/en<#Volume>)
- [type VolumeMount](https://www.daytona.io/docs/en<#VolumeMount>)


## Constants

<a name="CodeToolboxLanguageLabel"></a>

```go
const CodeToolboxLanguageLabel = "code-toolbox-language"
```

<a name="Chart"></a>
## type Chart


```go
type Chart = toolbox.Chart
```

<a name="CodeLanguage"></a>
## type CodeLanguage

CodeLanguage

```go
type CodeLanguage string
```

<a name="CodeLanguagePython"></a>

```go
const (
    CodeLanguagePython     CodeLanguage = "python"
    CodeLanguageJavaScript CodeLanguage = "javascript"
    CodeLanguageTypeScript CodeLanguage = "typescript"
)
```

<a name="CodeRunParams"></a>
## type CodeRunParams

CodeRunParams represents parameters for code execution

```go
type CodeRunParams struct {
    Argv []string
    Env  map[string]string
}
```

<a name="CreateSnapshotParams"></a>
## type CreateSnapshotParams

CreateSnapshotParams represents parameters for creating a snapshot

```go
type CreateSnapshotParams struct {
    Name           string
    Image          any // string or *Image
    Resources      *Resources
    Entrypoint     []string
    SkipValidation *bool
    SandboxClass   *SandboxClass
}
```

<a name="DaytonaConfig"></a>
## type DaytonaConfig

DaytonaConfig represents the configuration for the Daytona client. When a field is nil, the client will fall back to environment variables or defaults.

```go
type DaytonaConfig struct {
    APIKey         string
    JWTToken       string
    OrganizationID string
    APIUrl         string
    Target         string
    OtelEnabled    bool // Enable OpenTelemetry tracing for SDK operations.
    Experimental   *ExperimentalConfig
}
```

<a name="ExecuteResponse"></a>
## type ExecuteResponse

ExecuteResponse represents a command execution response

```go
type ExecuteResponse struct {
    ExitCode  int
    Result    string
    Artifacts *ExecutionArtifacts // nil when no artifacts available
}
```

<a name="ExecutionArtifacts"></a>
## type ExecutionArtifacts

ExecutionArtifacts represents execution output artifacts

```go
type ExecutionArtifacts struct {
    Stdout string
    Charts []Chart
}
```

<a name="ExecutionError"></a>
## type ExecutionError

ExecutionError represents a code execution error

```go
type ExecutionError struct {
    Name      string
    Value     string
    Traceback *string // Optional stack trace; nil when not available
}
```

<a name="ExecutionResult"></a>
## type ExecutionResult

ExecutionResult represents code interpreter execution result

```go
type ExecutionResult struct {
    Stdout string
    Stderr string
    Charts []Chart         // Optional charts from matplotlib
    Error  *ExecutionError // nil = success, non-nil = execution failed
}
```

<a name="ExperimentalConfig"></a>
## type ExperimentalConfig

ExperimentalConfig holds experimental feature flags for the Daytona client.

```go
type ExperimentalConfig struct {
    // Deprecated: use DaytonaConfig.OtelEnabled. Kept for backwards compatibility.
    OtelEnabled bool
}
```

<a name="FileDownloadRequest"></a>
## type FileDownloadRequest

FileDownloadRequest

```go
type FileDownloadRequest struct {
    Source      string
    Destination *string // nil = download to memory (return []byte), non-nil = save to file path
}
```

<a name="FileDownloadResponse"></a>
## type FileDownloadResponse

FileDownloadResponse represents a file download response

```go
type FileDownloadResponse struct {
    Source string
    Result any     // []byte or string (path)
    Error  *string // nil = success, non-nil = error message
}
```

<a name="FileInfo"></a>
## type FileInfo

FileInfo represents file metadata

```go
type FileInfo struct {
    Name         string
    Size         int64
    Mode         string
    ModifiedTime time.Time
    IsDirectory  bool
}
```

<a name="FileStatus"></a>
## type FileStatus

FileStatus represents the status of a file in git

```go
type FileStatus struct {
    Path   string
    Status string
}
```

<a name="FileUpload"></a>
## type FileUpload

FileUpload represents a file to upload

```go
type FileUpload struct {
    Source      any // []byte or string (path)
    Destination string
}
```

<a name="GitCommitResponse"></a>
## type GitCommitResponse

GitCommitResponse

```go
type GitCommitResponse struct {
    SHA string
}
```

<a name="GitStatus"></a>
## type GitStatus

GitStatus represents git repository status

```go
type GitStatus struct {
    CurrentBranch   string
    Ahead           int
    Behind          int
    BranchPublished bool
    FileStatus      []FileStatus
}
```

<a name="GpuType"></a>
## type GpuType

GpuType identifies a specific NVIDIA GPU model. Used in \[Resources.GpuType\] as an ordered preference list — the scheduler tries each in order and pins the sandbox/snapshot to the first that has capacity. It is an alias for the API client's GpuType type.

```go
type GpuType = apiclient.GpuType
```

<a name="GpuTypeH100"></a>

```go
const (
    GpuTypeH100       GpuType = apiclient.GPUTYPE_H100
    GpuTypeRtxPro6000 GpuType = apiclient.GPUTYPE_RTX_PRO_6000
)
```

<a name="ImageParams"></a>
## type ImageParams

ImageParams represents parameters for creating a sandbox from an image

```go
type ImageParams struct {
    SandboxBaseParams
    Image     any // string or *Image
    Resources *Resources
}
```

<a name="LspLanguageID"></a>
## type LspLanguageID


```go
type LspLanguageID string
```

<a name="LspLanguagePython"></a>

```go
const (
    LspLanguagePython     LspLanguageID = "python"
    LspLanguageJavaScript LspLanguageID = "javascript"
    LspLanguageTypeScript LspLanguageID = "typescript"
)
```

<a name="OutputMessage"></a>
## type OutputMessage

OutputMessage represents an output message

```go
type OutputMessage struct {
    Type      string `json:"type"`
    Text      string `json:"text"`
    Name      string `json:"name"`
    Value     string `json:"value"`
    Traceback string `json:"traceback"`
}
```

<a name="PaginatedSnapshots"></a>
## type PaginatedSnapshots

PaginatedSnapshots represents a paginated list of snapshots

```go
type PaginatedSnapshots struct {
    Items      []*Snapshot
    Total      int
    Page       int
    TotalPages int
}
```

<a name="Position"></a>
## type Position

Position represents a position in a document

```go
type Position struct {
    Line      int // zero-based
    Character int // zero-based
}
```

<a name="PreviewLink"></a>
## type PreviewLink

PreviewLink contains the URL and authentication token for a sandbox preview.

```go
type PreviewLink struct {
    URL   string
    Token string
}
```

<a name="PtyResult"></a>
## type PtyResult

PtyResult represents PTY session exit information

```go
type PtyResult struct {
    ExitCode *int    // nil = process still running, non-nil = exit code
    Error    *string // nil = success, non-nil = error message
}
```

<a name="PtySessionInfo"></a>
## type PtySessionInfo

PtySessionInfo represents PTY session information

```go
type PtySessionInfo struct {
    ID        string
    Active    bool
    CWD       string // Current working directory; may be empty unavailable
    Cols      int
    Rows      int
    ProcessID *int // Process ID; may be nil if unavailable
    CreatedAt time.Time
}
```

<a name="PtySize"></a>
## type PtySize

PtySize represents terminal dimensions

```go
type PtySize struct {
    Rows int
    Cols int
}
```

<a name="Resources"></a>
## type Resources

Resources represents resource allocation for a sandbox.

```go
type Resources struct {
    CPU     int
    GPU     int
    GpuType []GpuType
    Memory  int
    Disk    int
}
```

<a name="SandboxBaseParams"></a>
## type SandboxBaseParams

SandboxBaseParams contains common parameters for sandbox creation.

```go
type SandboxBaseParams struct {
    Name                string
    User                string
    Language            CodeLanguage
    EnvVars             map[string]string
    Labels              map[string]string
    Public              bool
    AutoStopInterval    *int // nil = no auto-stop, 0 = immediate stop
    AutoArchiveInterval *int // nil = no auto-archive, 0 = immediate archive
    AutoDeleteInterval  *int // nil = no auto-delete, 0 = immediate delete
    Volumes             []VolumeMount
    NetworkBlockAll     bool
    NetworkAllowList    *string
    Ephemeral           bool
    // LinkedSandbox is the ID or name of an existing sandbox to link the new sandbox to.
    // The new sandbox will be scheduled on the same runner as the linked sandbox so a local
    // network can be established between them.
    // Linked sandboxes must be ephemeral (AutoDeleteInterval=0) and cannot themselves be
    // linked to another sandbox.
    LinkedSandbox string
}
```

<a name="SandboxClass"></a>
## type SandboxClass

SandboxClass determines which runners can host sandboxes created from a snapshot. It is an alias for the API client's SandboxClass type.

```go
type SandboxClass = apiclient.SandboxClass
```

<a name="SandboxClassLinuxVM"></a>

```go
const (
    SandboxClassLinuxVM   SandboxClass = apiclient.SANDBOXCLASS_LINUX_VM
    SandboxClassContainer SandboxClass = apiclient.SANDBOXCLASS_CONTAINER
    SandboxClassAndroid   SandboxClass = apiclient.SANDBOXCLASS_ANDROID
)
```

<a name="ScreenshotOptions"></a>
## type ScreenshotOptions


```go
type ScreenshotOptions struct {
    ShowCursor *bool    // nil = default, true = show, false = hide
    Format     *string  // nil = default format (PNG), or "jpeg", "webp", etc.
    Quality    *int     // nil = default quality, 0-100 for JPEG/WebP
    Scale      *float64 // nil = 1.0, scaling factor for the screenshot
}
```

<a name="ScreenshotRegion"></a>
## type ScreenshotRegion

ScreenshotRegion represents a screenshot region

```go
type ScreenshotRegion struct {
    X      int
    Y      int
    Width  int
    Height int
}
```

<a name="ScreenshotResponse"></a>
## type ScreenshotResponse


```go
type ScreenshotResponse struct {
    Image     string // base64-encoded image data
    Width     int
    Height    int
    SizeBytes *int // Size in bytes
}
```

<a name="SignedPreviewLink"></a>
## type SignedPreviewLink

SignedPreviewLink contains the signed URL, authentication token, port, and sandbox ID for a sandbox preview.

```go
type SignedPreviewLink struct {
    SandboxID string
    Port      int
    Token     string
    URL       string
}
```

<a name="Snapshot"></a>
## type Snapshot

Snapshot represents a Daytona snapshot

```go
type Snapshot struct {
    ID             string     `json:"id"`
    OrganizationID string     `json:"organizationId,omitempty"`
    General        bool       `json:"general"`
    Name           string     `json:"name"`
    ImageName      string     `json:"imageName,omitempty"`
    State          string     `json:"state"`
    Size           *float64   `json:"size,omitempty"`
    Entrypoint     []string   `json:"entrypoint,omitempty"`
    CPU            int        `json:"cpu"`
    GPU            int        `json:"gpu"`
    Memory         int        `json:"mem"` // API uses "mem" not "memory"
    Disk           int        `json:"disk"`
    ErrorReason    *string    `json:"errorReason,omitempty"` // nil = success, non-nil = error reason if snapshot failed
    SkipValidation bool       `json:"skipValidation"`
    CreatedAt      time.Time  `json:"createdAt"`
    UpdatedAt      time.Time  `json:"updatedAt"`
    LastUsedAt     *time.Time `json:"lastUsedAt,omitempty"`
}
```

<a name="SnapshotParams"></a>
## type SnapshotParams

SnapshotParams represents parameters for creating a sandbox from a snapshot

```go
type SnapshotParams struct {
    SandboxBaseParams
    Snapshot string
}
```

<a name="Volume"></a>
## type Volume

Volume represents a Daytona volume

```go
type Volume struct {
    ID             string    `json:"id"`
    Name           string    `json:"name"`
    OrganizationID string    `json:"organizationId"`
    State          string    `json:"state"`
    ErrorReason    *string   `json:"errorReason,omitempty"`
    CreatedAt      time.Time `json:"createdAt"`
    UpdatedAt      time.Time `json:"updatedAt"`
    LastUsedAt     time.Time `json:"lastUsedAt,omitempty"`
}
```

<a name="VolumeMount"></a>
## type VolumeMount

VolumeMount represents a volume mount configuration

```go
type VolumeMount struct {
    VolumeID  string // ID or name of the volume to mount
    MountPath string
    Subpath   *string // Optional subpath within the volume; nil = mount entire volume
}
```
