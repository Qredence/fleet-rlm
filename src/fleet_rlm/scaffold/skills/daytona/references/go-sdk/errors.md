

# errors


## Contents

- Index
- func ConvertAPIError
- func ConvertToolboxError
- func NewDaytonaErrorFromBody
- type DaytonaAuthenticationError
- type DaytonaConflictError
- type DaytonaError
- type DaytonaForbiddenError
- type DaytonaNotFoundError
- type DaytonaRateLimitError
- type DaytonaServerError
- type DaytonaTimeoutError
- type DaytonaValidationError
- See Also

```go
import "github.com/daytonaio/daytona/libs/sdk-go/pkg/errors"
```

## Index

- [func ConvertAPIError\(err error, httpResp \*http.Response\) error](https://www.daytona.io/docs/en<#ConvertAPIError>)
- [func ConvertToolboxError\(err error, httpResp \*http.Response\) error](https://www.daytona.io/docs/en<#ConvertToolboxError>)
- [func NewDaytonaErrorFromBody\(body \[\]byte, statusCode int, headers http.Header\) error](<#NewDaytonaErrorFromBody>)
- [type DaytonaAuthenticationError](https://www.daytona.io/docs/en<#DaytonaAuthenticationError>)
  - [func NewDaytonaAuthenticationError\(message string, headers http.Header\) \*DaytonaAuthenticationError](https://www.daytona.io/docs/en<#NewDaytonaAuthenticationError>)
  - [func \(e \*DaytonaAuthenticationError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaAuthenticationError.Error>)
- [type DaytonaConflictError](https://www.daytona.io/docs/en<#DaytonaConflictError>)
  - [func NewDaytonaConflictError\(message string, headers http.Header\) \*DaytonaConflictError](https://www.daytona.io/docs/en<#NewDaytonaConflictError>)
  - [func \(e \*DaytonaConflictError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaConflictError.Error>)
- [type DaytonaError](https://www.daytona.io/docs/en<#DaytonaError>)
  - [func NewDaytonaError\(message string, statusCode int, headers http.Header\) \*DaytonaError](https://www.daytona.io/docs/en<#NewDaytonaError>)
  - [func \(e \*DaytonaError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaError.Error>)
- [type DaytonaForbiddenError](https://www.daytona.io/docs/en<#DaytonaForbiddenError>)
  - [func NewDaytonaForbiddenError\(message string, headers http.Header\) \*DaytonaForbiddenError](https://www.daytona.io/docs/en<#NewDaytonaForbiddenError>)
  - [func \(e \*DaytonaForbiddenError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaForbiddenError.Error>)
- [type DaytonaNotFoundError](https://www.daytona.io/docs/en<#DaytonaNotFoundError>)
  - [func NewDaytonaNotFoundError\(message string, headers http.Header\) \*DaytonaNotFoundError](https://www.daytona.io/docs/en<#NewDaytonaNotFoundError>)
  - [func \(e \*DaytonaNotFoundError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaNotFoundError.Error>)
- [type DaytonaRateLimitError](https://www.daytona.io/docs/en<#DaytonaRateLimitError>)
  - [func NewDaytonaRateLimitError\(message string, headers http.Header\) \*DaytonaRateLimitError](https://www.daytona.io/docs/en<#NewDaytonaRateLimitError>)
  - [func \(e \*DaytonaRateLimitError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaRateLimitError.Error>)
- [type DaytonaServerError](https://www.daytona.io/docs/en<#DaytonaServerError>)
  - [func NewDaytonaServerError\(message string, statusCode int, headers http.Header\) \*DaytonaServerError](https://www.daytona.io/docs/en<#NewDaytonaServerError>)
  - [func \(e \*DaytonaServerError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaServerError.Error>)
- [type DaytonaTimeoutError](https://www.daytona.io/docs/en<#DaytonaTimeoutError>)
  - [func NewDaytonaTimeoutError\(message string\) \*DaytonaTimeoutError](https://www.daytona.io/docs/en<#NewDaytonaTimeoutError>)
  - [func \(e \*DaytonaTimeoutError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaTimeoutError.Error>)
- [type DaytonaValidationError](https://www.daytona.io/docs/en<#DaytonaValidationError>)
  - [func NewDaytonaValidationError\(message string, headers http.Header\) \*DaytonaValidationError](https://www.daytona.io/docs/en<#NewDaytonaValidationError>)
  - [func \(e \*DaytonaValidationError\) Error\(\) string](https://www.daytona.io/docs/en<#DaytonaValidationError.Error>)


<a name="ConvertAPIError"></a>
## func ConvertAPIError

```go
func ConvertAPIError(err error, httpResp *http.Response) error
```

ConvertAPIError converts api\-client\-go errors to SDK error types

<a name="ConvertToolboxError"></a>
## func ConvertToolboxError

```go
func ConvertToolboxError(err error, httpResp *http.Response) error
```

ConvertToolboxError converts toolbox\-api\-client\-go errors to SDK error types

<a name="NewDaytonaErrorFromBody"></a>
## func NewDaytonaErrorFromBody

```go
func NewDaytonaErrorFromBody(body []byte, statusCode int, headers http.Header) error
```

NewDaytonaErrorFromBody parses a JSON response body and maps the status code to the appropriate SDK error type. Falls back to the raw body as the message.

<a name="DaytonaAuthenticationError"></a>
## type DaytonaAuthenticationError

DaytonaAuthenticationError represents an authentication error \(401\)

```go
type DaytonaAuthenticationError struct {
    *DaytonaError
}
```

<a name="NewDaytonaAuthenticationError"></a>
### func NewDaytonaAuthenticationError

```go
func NewDaytonaAuthenticationError(message string, headers http.Header) *DaytonaAuthenticationError
```


<a name="DaytonaAuthenticationError.Error"></a>
### func \(\*DaytonaAuthenticationError\) Error

```go
func (e *DaytonaAuthenticationError) Error() string
```


<a name="DaytonaConflictError"></a>
## type DaytonaConflictError

DaytonaConflictError represents a conflict error \(409\)

```go
type DaytonaConflictError struct {
    *DaytonaError
}
```

<a name="NewDaytonaConflictError"></a>
### func NewDaytonaConflictError

```go
func NewDaytonaConflictError(message string, headers http.Header) *DaytonaConflictError
```


<a name="DaytonaConflictError.Error"></a>
### func \(\*DaytonaConflictError\) Error

```go
func (e *DaytonaConflictError) Error() string
```


<a name="DaytonaError"></a>
## type DaytonaError

DaytonaError is the base error type for all Daytona SDK errors

```go
type DaytonaError struct {
    Message    string
    StatusCode int
    Headers    http.Header
}
```

<a name="NewDaytonaError"></a>
### func NewDaytonaError

```go
func NewDaytonaError(message string, statusCode int, headers http.Header) *DaytonaError
```

NewDaytonaError creates a new DaytonaError

<a name="DaytonaError.Error"></a>
### func \(\*DaytonaError\) Error

```go
func (e *DaytonaError) Error() string
```


<a name="DaytonaForbiddenError"></a>
## type DaytonaForbiddenError

DaytonaForbiddenError represents a forbidden/authorization error \(403\)

```go
type DaytonaForbiddenError struct {
    *DaytonaError
}
```

<a name="NewDaytonaForbiddenError"></a>
### func NewDaytonaForbiddenError

```go
func NewDaytonaForbiddenError(message string, headers http.Header) *DaytonaForbiddenError
```


<a name="DaytonaForbiddenError.Error"></a>
### func \(\*DaytonaForbiddenError\) Error

```go
func (e *DaytonaForbiddenError) Error() string
```


<a name="DaytonaNotFoundError"></a>
## type DaytonaNotFoundError

DaytonaNotFoundError represents a resource not found error \(404\)

```go
type DaytonaNotFoundError struct {
    *DaytonaError
}
```

<a name="NewDaytonaNotFoundError"></a>
### func NewDaytonaNotFoundError

```go
func NewDaytonaNotFoundError(message string, headers http.Header) *DaytonaNotFoundError
```

NewDaytonaNotFoundError creates a new DaytonaNotFoundError

<a name="DaytonaNotFoundError.Error"></a>
### func \(\*DaytonaNotFoundError\) Error

```go
func (e *DaytonaNotFoundError) Error() string
```


<a name="DaytonaRateLimitError"></a>
## type DaytonaRateLimitError

DaytonaRateLimitError represents a rate limit error \(429\)

```go
type DaytonaRateLimitError struct {
    *DaytonaError
}
```

<a name="NewDaytonaRateLimitError"></a>
### func NewDaytonaRateLimitError

```go
func NewDaytonaRateLimitError(message string, headers http.Header) *DaytonaRateLimitError
```

NewDaytonaRateLimitError creates a new DaytonaRateLimitError

<a name="DaytonaRateLimitError.Error"></a>
### func \(\*DaytonaRateLimitError\) Error

```go
func (e *DaytonaRateLimitError) Error() string
```


<a name="DaytonaServerError"></a>
## type DaytonaServerError

DaytonaServerError represents a server error \(5xx\)

```go
type DaytonaServerError struct {
    *DaytonaError
}
```

<a name="NewDaytonaServerError"></a>
### func NewDaytonaServerError

```go
func NewDaytonaServerError(message string, statusCode int, headers http.Header) *DaytonaServerError
```


<a name="DaytonaServerError.Error"></a>
### func \(\*DaytonaServerError\) Error

```go
func (e *DaytonaServerError) Error() string
```


<a name="DaytonaTimeoutError"></a>
## type DaytonaTimeoutError

DaytonaTimeoutError represents a timeout error

```go
type DaytonaTimeoutError struct {
    *DaytonaError
}
```

<a name="NewDaytonaTimeoutError"></a>
### func NewDaytonaTimeoutError

```go
func NewDaytonaTimeoutError(message string) *DaytonaTimeoutError
```


<a name="DaytonaTimeoutError.Error"></a>
### func \(\*DaytonaTimeoutError\) Error

```go
func (e *DaytonaTimeoutError) Error() string
```


<a name="DaytonaValidationError"></a>
## type DaytonaValidationError

DaytonaValidationError represents a validation/bad request error \(400\)

```go
type DaytonaValidationError struct {
    *DaytonaError
}
```

<a name="NewDaytonaValidationError"></a>
### func NewDaytonaValidationError

```go
func NewDaytonaValidationError(message string, headers http.Header) *DaytonaValidationError
```


<a name="DaytonaValidationError.Error"></a>
### func \(\*DaytonaValidationError\) Error

```go
func (e *DaytonaValidationError) Error() string
```

## See Also
- [TypeScript SDK - errors](../typescript-sdk/errors.md)
