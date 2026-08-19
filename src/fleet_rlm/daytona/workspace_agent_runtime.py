"""Stdlib-only Session Workspace agent executed inside a Daytona sandbox.

The packaged module is the complete remote artifact.  ``handle(request)`` is
the only operation entrypoint used by both the installed and fallback launchers;
the host never edits, searches, or re-indents this source.
"""

import base64, datetime, errno, fcntl, hashlib, json, os, re, stat, time
from typing import NoReturn
# Portable errno membership sets owned by this remote program (include numeric
# ENOSYS/EOPNOTSUPP literals that some volume backends surface without names).
_UNSUPPORTED_LINK_ERRNOS = frozenset({errno.EPERM})
_UNSUPPORTED_REPLACE_ERRNOS = frozenset(
    {
        errno.EPERM,
        errno.EXDEV,
        errno.EOPNOTSUPP,
        getattr(errno, 'ENOTSUP', errno.EOPNOTSUPP),
        errno.ENOSYS,
        38,
        95,
    }
)
_WORM_RECREATE_ERRNOS = frozenset(
    {
        errno.EPERM,
        errno.EBADF,
        errno.ENOSYS,
        errno.EOPNOTSUPP,
        getattr(errno, 'ENOTSUP', errno.EOPNOTSUPP),
    }
)
class _AgentResponse(Exception):
    """Internal non-local return used to preserve the existing branch exits."""

    def __init__(self, payload):
        super().__init__()
        self.payload = payload


def respond(payload) -> NoReturn:
    raise _AgentResponse(payload)


def fail(error, **extra) -> NoReturn:
    respond({'ok': False, 'error': error, **extra})


AGENT_PROTOCOL_VERSION = 'fleet.workspace-agent/v1'
AGENT_SUPPORTED_OPERATIONS = (
    'list',
    'stat',
    'tail_read',
    'read',
    'read_page',
    'append',
    'memory_migrate',
    'memory_append',
    'memory_edit',
    'memory_delete',
    'unlink',
    'delete',
    'patch',
    'write',
)
AGENT_REQUEST_MAX_BYTES = 16 * 1024 * 1024
AGENT_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
AGENT_CAPABILITIES = {
    'locking': 'fcntl_flock_inode_revalidation',
    'replacement': 'replace_overwrite_recreate',
    'fallback': 'non_atomic_overwrite_cleanup_warning',
}


def _artifact_checksum():
    """Return the installed module's checksum, or no value for source exec."""
    try:
        artifact_path = __file__
    except NameError:
        return None
    try:
        with open(artifact_path, 'rb') as artifact:
            return hashlib.sha256(artifact.read()).hexdigest()
    except OSError:
        return None


def get_metadata():
    """Expose protocol metadata without requiring an operation request."""
    return {
        'protocol_version': AGENT_PROTOCOL_VERSION,
        'source_checksum': _artifact_checksum(),
        'operations': AGENT_SUPPORTED_OPERATIONS,
        'request_max_bytes': AGENT_REQUEST_MAX_BYTES,
        'response_max_bytes': AGENT_RESPONSE_MAX_BYTES,
        **AGENT_CAPABILITIES,
    }


AGENT_METADATA = get_metadata()
def decode_page(data):
    try:
        return data.decode('utf-8'), len(data)
    except UnicodeDecodeError as exc:
        if exc.end == len(data) and ('end' in exc.reason or 'truncated' in exc.reason):
            valid = data[:exc.start]
            return valid.decode('utf-8'), len(valid)
        raise
def check_memory_log_encoding(data):
    # The existing log body gates appends only on UTF-8 decodability:
    # humans edit MEMORIES.md, so one malformed text line must stay
    # tolerated (the appended record itself is host-validated strictly).
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        raise StorageError(errno.EILSEQ)
memory_record = re.compile(r'- \[(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\] \*\*(?P<category>[A-Za-z0-9][A-Za-z0-9 _-]*)\*\*(?: <!-- id:(?P<memory_id>[0-9a-f]{8})(?: source:(?P<source>user_explicit|agent_candidate|operator_import|legacy_unknown) updated:(?P<updated_at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)(?: supersedes:(?P<supersedes_id>[0-9a-f]{8}))?)? -->)?: (?P<learning>[^\r\n]+)\n')
def effective_memory_id(match, ordinal=0):
    persisted = match.group('memory_id')
    if persisted is not None:
        return persisted
    canonical = f"- [{match.group('timestamp')}] **{match.group('category')}**: {match.group('learning')}"
    if ordinal:
        canonical += f'\n# legacy occurrence {ordinal}'
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:8]
class UnsafePath(Exception):
    pass
class StorageError(Exception):
    def __init__(self, errno_value):
        super().__init__(errno_value)
        self.errno = errno_value
class ReplacementUnsupported(Exception):
    def __init__(self, errno_value):
        super().__init__(errno_value)
        self.errno = errno_value
def open_directory(path, *, dir_fd=None, create=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        existing = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(existing.st_mode):
            raise UnsafePath(path)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return os.open(path, flags, dir_fd=dir_fd)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(path, 0o700, dir_fd=dir_fd)
        except FileExistsError:
            pass
        return os.open(path, flags, dir_fd=dir_fd)
def open_chain(*, volume_root, root, create=False):
    fds = []
    try:
        volume_fd = open_directory(volume_root)
        fds.append(volume_fd)
        root_parts = os.path.relpath(root, volume_root).split(os.sep)
        if root_parts == ['.'] or root_parts == ['']:
            root_parts = []
        for part in root_parts:
            next_fd = open_directory(part, dir_fd=fds[-1], create=create)
            fds.append(next_fd)
        root_fd = fds[-1]
        return fds, root_fd
    except BaseException:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass
        raise
def split_relative(value):
    return [] if value == '.' else value.split('/')
def open_parent(root_fd, parts, *, create=False):
    fds = []
    current_fd = root_fd
    try:
        for part in parts:
            current_fd = open_directory(part, dir_fd=current_fd, create=create)
            fds.append(current_fd)
        return fds, current_fd
    except BaseException:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass
        raise
def close_all(fds):
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError:
            pass
def entry_for(info, entry_path):
    modified_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(info.st_mtime))
    # ``is_regular_file`` is additive: stat consumers must reject FIFOs/
    # device nodes before any blocking read open; ``kind`` is unchanged.
    if stat.S_ISDIR(info.st_mode):
        return {'path': entry_path, 'kind': 'directory', 'byte_size': None, 'modified_at': modified_at, 'is_regular_file': False}
    if not stat.S_ISREG(info.st_mode):
        return {'path': entry_path, 'kind': 'file', 'byte_size': None, 'modified_at': modified_at, 'is_regular_file': False}
    return {'path': entry_path, 'kind': 'file', 'byte_size': info.st_size, 'modified_at': modified_at, 'is_regular_file': True}
def write_all(fd, payload):
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(fd, payload[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError('short write')
        offset += written
def fsync_directory(parent_fd):
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise StorageError(exc.errno) from exc
def replace_existing(parent_fd, name, payload):
    temporary = f'.fleet-write-{os.getpid()}-{time.time_ns()}'
    fd = None
    temporary_removed = False
    cleanup_errno = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary_removed = True
        try:
            fsync_directory(parent_fd)
        except StorageError as exc:
            cleanup_errno = exc.errno
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno
    except (FileNotFoundError, FileExistsError):
        raise
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_REPLACE_ERRNOS:
            raise ReplacementUnsupported(exc.errno) from exc
        raise StorageError(exc.errno) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if not temporary_removed:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
def write_new_direct(parent_fd, name, payload):
    fd = None
    created_stat = None
    def cleanup_created():
        if created_stat is None:
            return
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (created_stat.st_dev, created_stat.st_ino):
                os.unlink(name, dir_fd=parent_fd)
        except OSError:
            pass
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
        created_stat = os.fstat(fd)
        write_all(fd, payload)
        os.fsync(fd)
        fsync_directory(parent_fd)
        return os.fstat(fd)
    except FileExistsError:
        raise
    except OSError as exc:
        cleanup_created()
        raise StorageError(exc.errno) from exc
    except BaseException:
        cleanup_created()
        raise
    finally:
        if fd is not None:
            os.close(fd)
def publish_new(parent_fd, name, payload):
    temporary = f'.fleet-write-{os.getpid()}-{time.time_ns()}'
    fd = None
    temporary_removed = False
    cleanup_errno = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=parent_fd)
        write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        try:
            os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            pass
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise FileExistsError(name) from exc
            if exc.errno not in _UNSUPPORTED_LINK_ERRNOS:
                raise StorageError(exc.errno) from exc
            direct_stat = write_new_direct(parent_fd, name, payload)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
                temporary_removed = True
            except OSError as cleanup_exc:
                cleanup_errno = cleanup_exc.errno
            return direct_stat, cleanup_errno
        fsync_directory(parent_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
            temporary_removed = True
        except OSError as exc:
            cleanup_errno = exc.errno
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno
    finally:
        if fd is not None:
            os.close(fd)
        if not temporary_removed:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
            else:
                temporary_removed = True
def read_existing(parent_fd, name, max_bytes, expected_stat=None):
    fd = None
    try:
        if expected_stat is not None and not stat.S_ISREG(expected_stat.st_mode):
            raise StorageError(errno.EPERM)
        fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise StorageError(errno.EPERM)
        if expected_stat is not None and (
            (opened_stat.st_dev, opened_stat.st_ino) != (expected_stat.st_dev, expected_stat.st_ino)
            or opened_stat.st_size != expected_stat.st_size
        ):
            raise StorageError(errno.EPERM)
        data = b''
        while len(data) <= max_bytes:
            chunk = os.read(fd, max_bytes + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > max_bytes:
            raise StorageError(errno.EFBIG)
        return data
    except OSError as exc:
        if isinstance(exc, StorageError):
            raise
        raise StorageError(exc.errno) from exc
    finally:
        if fd is not None:
            os.close(fd)
def lock_memory_change(parent_fd, name):
    # A stable volume-root lock inode bounds one
    # read/validate/compose/publish-memory sequence across processes.
    lock_name = name + '.lock'
    attempts = 0
    while True:
        fd = None
        try:
            fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600, dir_fd=parent_fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked_stat = os.fstat(fd)
            current_stat = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISREG(locked_stat.st_mode) and (locked_stat.st_dev, locked_stat.st_ino) == (current_stat.st_dev, current_stat.st_ino):
                return fd, locked_stat
            os.close(fd)
        except OSError as exc:
            if fd is not None and fd >= 0:
                try: os.close(fd)
                except OSError: pass
            if exc.errno not in (errno.ENOENT, errno.EINTR):
                raise StorageError(exc.errno) from exc
        attempts += 1
        if attempts >= 8:
            raise StorageError(errno.EBUSY)
def lock_existing(parent_fd, name):
    # File lock + inode revalidation protects the compare/mutate
    # window across independent mounted Sandboxes that honor POSIX locks.
    attempts = 0
    while True:
        fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked_stat = os.fstat(fd)
            current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (locked_stat.st_dev, locked_stat.st_ino) == (current_stat.st_dev, current_stat.st_ino):
                return fd, locked_stat
            os.close(fd)
        except OSError as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if exc.errno == errno.ENOENT:
                raise FileNotFoundError(name) from exc
            raise StorageError(exc.errno) from exc
        attempts += 1
        if attempts >= 8:
            raise StorageError(errno.EBUSY)
def overwrite_existing_direct(parent_fd, name, payload, previous):
    fd = None
    cleanup_errno = None
    try:
        fd = os.open(name, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)
        write_all(fd, payload)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
            fd = None
        try:
            restore_fd = os.open(name, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)
            try:
                write_all(restore_fd, previous)
                os.fsync(restore_fd)
            finally:
                os.close(restore_fd)
        except OSError as restore_exc:
            raise StorageError(restore_exc.errno) from restore_exc
        raise StorageError(exc.errno) from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        cleanup_errno = exc.errno
    finally:
        if fd is not None:
            os.close(fd)
    try:
        fsync_directory(parent_fd)
    except StorageError as exc:
        cleanup_errno = exc.errno
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno
def recreate_existing(parent_fd, name, payload, previous):
    # WORM-like Volume backends reject every atomic rename/link and
    # every in-place write-open on existing files (EPERM); unlink and
    # O_EXCL recreate is the only accepted mutation. A failed mutation
    # restores the previous bytes best-effort so the log is never
    # silently destroyed.
    def create_fresh(data):
        created = None
        attempts = 0
        while created is None:
            attempts += 1
            try:
                created = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=parent_fd)
            except FileNotFoundError:
                if attempts >= 25:
                    raise StorageError(errno.ENOENT)
                time.sleep(0.02)
        try:
            write_all(created, data)
            os.fsync(created)
        except BaseException:
            try:
                os.close(created)
            except OSError:
                pass
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        os.close(created)
        try:
            fsync_directory(parent_fd)
        except StorageError:
            pass
    cleanup_errno = None
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    try:
        create_fresh(payload)
    except BaseException as exc:
        try:
            create_fresh(previous)
        except BaseException:
            pass
        if isinstance(exc, StorageError):
            raise
        raise StorageError(exc.errno if isinstance(exc, OSError) else errno.EIO) from exc
    try:
        fsync_directory(parent_fd)
    except StorageError as exc:
        cleanup_errno = exc.errno
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False), cleanup_errno


def _valid_root_paths(volume_root, root, operation):
    if not isinstance(volume_root, str) or not isinstance(root, str):
        return False
    if not os.path.isabs(volume_root) or not os.path.isabs(root):
        return False
    if '\x00' in volume_root or '\x00' in root:
        return False
    normalized_volume = os.path.normpath(volume_root)
    normalized_root = os.path.normpath(root)
    if normalized_volume == normalized_root:
        return operation in ('stat', 'memory_migrate')
    try:
        if os.path.commonpath((normalized_volume, normalized_root)) != normalized_volume:
            return False
    except ValueError:
        return False
    relative_root = os.path.relpath(normalized_root, normalized_volume)
    return '..' not in relative_root.split(os.sep)


def _valid_relative_path(relative, operation):
    if not isinstance(relative, str) or not relative or '\x00' in relative or '\\' in relative:
        return False
    if relative == '.':
        return True
    if relative.startswith('/') or relative.startswith('./') or '//' in relative or relative.endswith('/'):
        return False
    parts = relative.split('/')
    if len(parts) > 8:
        return False
    return all(part not in ('', '.', '..', '.fleet') and len(part.encode('utf-8')) <= 255 for part in parts)


def _valid_request_values(request):
    boolean_fields = ('allow_missing', 'overwrite', 'checksum')
    string_fields = ('content_b64', 'after', 'memory_id', 'expected_sha256')
    integer_fields = ('max_bytes', 'limit', 'offset', 'max_chars', 'total_file_bytes')
    if any(type(request.get(name, False)) is not bool for name in boolean_fields):
        return False
    if any(not isinstance(request.get(name, ''), str) for name in string_fields):
        return False
    if any(type(request.get(name, 0)) is not int or request.get(name, 0) < 0 for name in integer_fields):
        return False
    return (
        request.get('max_bytes', 0) <= AGENT_RESPONSE_MAX_BYTES
        and request.get('limit', 0) <= 100
        and request.get('max_chars', 0) <= 10_000
        and request.get('total_file_bytes', 0) <= AGENT_RESPONSE_MAX_BYTES
    )


def handle(request):
    """Validate and execute one workspace request."""
    if not isinstance(request, dict):
        return {'ok': False, 'error': 'request_invalid'}
    try:
        encoded_request = json.dumps(request, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'request_invalid'}
    if len(encoded_request.encode('utf-8')) > AGENT_REQUEST_MAX_BYTES:
        return {'ok': False, 'error': 'request_invalid'}
    if request.get('protocol_version') != AGENT_PROTOCOL_VERSION:
        return {'ok': False, 'error': 'protocol_mismatch'}
    if request.get('operation') == '__handshake__':
        return {'ok': True, 'kind': 'workspace_agent_handshake', **get_metadata()}
    required = ('volume_root', 'root', 'operation', 'relative')
    if any(name not in request for name in required):
        return {'ok': False, 'error': 'request_invalid'}
    if any(not isinstance(request[name], str) for name in ('volume_root', 'root', 'operation', 'relative')):
        return {'ok': False, 'error': 'request_invalid'}
    if request['operation'] not in AGENT_SUPPORTED_OPERATIONS:
        return {'ok': False, 'error': 'unsupported'}
    if not _valid_root_paths(request['volume_root'], request['root'], request['operation']):
        return {'ok': False, 'error': 'request_invalid'}
    if not _valid_relative_path(request['relative'], request['operation']):
        return {'ok': False, 'error': 'request_invalid'}
    if not _valid_request_values(request):
        return {'ok': False, 'error': 'request_invalid'}
    volume_root = request['volume_root']
    root = request['root']
    relative = request['relative']
    operation = request['operation']
    allow_missing = request.get('allow_missing', False)
    max_bytes = request.get('max_bytes', 0)
    limit = request.get('limit', 0)
    overwrite = request.get('overwrite', False)
    content_b64 = request.get('content_b64', '')
    after = request.get('after', '')
    offset = request.get('offset', 0)
    max_chars = request.get('max_chars', 0)
    total_file_bytes = request.get('total_file_bytes', 0)
    checksum = request.get('checksum', False)
    memory_id = request.get('memory_id', '')
    expected_sha256 = request.get('expected_sha256', '')
    if not isinstance(content_b64, str) or not isinstance(expected_sha256, str):
        return {'ok': False, 'error': 'request_invalid'}
    locked_fd = None
    base_fds = []
    append_memory_id = ''
    try:
        try:
            base_fds, root_fd = open_chain(
                volume_root=volume_root,
                root=root,
                create=operation in ('write', 'append', 'memory_append'),
            )
        except FileNotFoundError:
            if relative == '.' and operation == 'list':
                respond({'ok': True, 'entries': [], 'truncated': False})
            if relative == '.' and operation == 'stat':
                respond({'ok': True, 'entry': None})
            raise
        relative_parts = split_relative(relative)
        if operation == 'list':
            target_fds, target_fd = open_parent(root_fd, relative_parts)
            try:
                candidates = []
                with os.scandir(target_fd) as scanner:
                    for item in scanner:
                        child_relative = item.name if relative == '.' else f'{relative}/{item.name}'
                        if after and child_relative <= after:
                            continue
                        candidate = (child_relative, item.name)
                        if len(candidates) < limit + 1:
                            candidates.append(candidate)
                            continue
                        largest = max(range(len(candidates)), key=lambda index: candidates[index][0])
                        if child_relative < candidates[largest][0]:
                            candidates[largest] = candidate
                candidates.sort(key=lambda value: value[0])
                selected = candidates[:limit]
                entries = []
                for child_relative, child_name in selected:
                    child_stat = os.stat(child_name, dir_fd=target_fd, follow_symlinks=False)
                    entries.append(entry_for(child_stat, child_relative))
                truncated = len(candidates) > limit
                next_cursor = selected[-1][0] if truncated and selected else None
            finally:
                close_all(target_fds)
            respond({'ok': True, 'entries': entries, 'truncated': truncated, 'next_cursor': next_cursor})
        if operation == 'stat':
            if not relative_parts:
                respond({'ok': True, 'entry': entry_for(os.fstat(root_fd), '.')})
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            fd = None
            try:
                try:
                    target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    if allow_missing:
                        respond({'ok': True, 'entry': None})
                    raise
                if stat.S_ISLNK(target_stat.st_mode):
                    fail('unsafe')
                entry = entry_for(target_stat, relative)
                if checksum and stat.S_ISREG(target_stat.st_mode):
                    if target_stat.st_size > max_bytes:
                        fail('read_bound')
                    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                    fd = os.open(relative_parts[-1], flags, dir_fd=parent_fd)
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(fd, 1048576)
                        if not chunk:
                            break
                        digest.update(chunk)
                    entry['checksum'] = digest.hexdigest()
                respond({'ok': True, 'entry': entry})
            finally:
                if fd is not None:
                    os.close(fd)
                close_all(parent_fds)
        if operation == 'tail_read':
            if not relative_parts:
                fail('is_directory')
            if max_bytes < 1:
                fail('read_bound')
            if total_file_bytes < 1:
                fail('read_bound')
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            fd = None
            try:
                try:
                    target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    if allow_missing:
                        response = {'ok': True, 'content': '', 'truncated': False}
                        response.update({'bytes_returned': 0, 'total_bytes': 0})
                        respond(response)
                    raise
                if not stat.S_ISREG(target_stat.st_mode):
                    fail('unsafe')
                if target_stat.st_size > total_file_bytes:
                    fail('too_large')
                flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
                fd = os.open(relative_parts[-1], flags, dir_fd=parent_fd)
                opened_stat = os.fstat(fd)
                if not stat.S_ISREG(opened_stat.st_mode):
                    fail('unsafe')
                if (opened_stat.st_dev, opened_stat.st_ino) != (target_stat.st_dev, target_stat.st_ino):
                    fail('unsafe')
                if opened_stat.st_size > total_file_bytes:
                    fail('too_large')
                target_stat = opened_stat
                read_size = min(target_stat.st_size, max_bytes)
                read_offset = target_stat.st_size - read_size
                preceding = b''
                if read_offset:
                    os.lseek(fd, read_offset - 1, os.SEEK_SET)
                    preceding = os.read(fd, 1)
                os.lseek(fd, read_offset, os.SEEK_SET)
                data = b''
                while len(data) < read_size:
                    chunk = os.read(fd, read_size - len(data))
                    if not chunk:
                        break
                    data += chunk
            finally:
                if fd is not None:
                    os.close(fd)
                close_all(parent_fds)
            truncated = target_stat.st_size > read_size
            if truncated and not (preceding == b'\n' and data.startswith(b'- [')):
                boundary = data.find(b'\n')
                data = b'' if boundary < 0 else data[boundary + 1:]
            if data and not data.endswith(b'\n'):
                final_boundary = data.rfind(b'\n')
                data = b'' if final_boundary < 0 else data[:final_boundary + 1]
                truncated = True
            try:
                content = data.decode('utf-8')
            except UnicodeDecodeError:
                fail('invalid_utf8')
            respond({'ok': True, 'content': content, 'truncated': truncated, 'bytes_returned': len(data), 'total_bytes': target_stat.st_size})
        if operation in ('read', 'read_page'):
            if not relative_parts:
                fail('is_directory')
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            fd = None
            try:
                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(target_stat.st_mode):
                    fail('unsafe')
                if stat.S_ISDIR(target_stat.st_mode):
                    fail('is_directory')
                if not stat.S_ISREG(target_stat.st_mode):
                    fail('unsafe')
                if target_stat.st_size > max_bytes:
                    fail('read_bound')
                if operation == 'read':
                    read_offset = 0
                    read_limit = max_bytes + 1
                else:
                    if type(offset) is not int or offset < 0 or offset > target_stat.st_size:
                        fail('cursor')
                    read_offset = offset
                    read_limit = min(target_stat.st_size - read_offset, max_chars * 4 + 4)
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                fd = os.open(relative_parts[-1], flags, dir_fd=parent_fd)
                opened_stat = os.fstat(fd)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or (opened_stat.st_dev, opened_stat.st_ino) != (target_stat.st_dev, target_stat.st_ino)
                    or opened_stat.st_size != target_stat.st_size
                ):
                    fail('unsafe')
                target_stat = opened_stat
                if operation == 'read_page':
                    os.lseek(fd, read_offset, os.SEEK_SET)
                data = b''
                while len(data) < read_limit:
                    chunk = os.read(fd, read_limit - len(data))
                    if not chunk:
                        break
                    data += chunk
                if operation == 'read' and len(data) != target_stat.st_size:
                    fail('unsafe')
            except FileNotFoundError:
                fail('not_found')
            finally:
                if fd is not None:
                    os.close(fd)
                close_all(parent_fds)
            if operation == 'read' and len(data) > max_bytes:
                fail('read_bound')
            try:
                content, valid_bytes = decode_page(data)
            except UnicodeDecodeError:
                fail('invalid_utf8')
            if operation == 'read':
                respond({'ok': True, 'content': content})
            if len(content) > max_chars:
                content = content[:max_chars]
            consumed = len(content[:max_chars].encode('utf-8'))
            next_offset = read_offset + consumed
            eof = next_offset >= target_stat.st_size
            respond({'ok': True, 'content': content, 'byte_size': target_stat.st_size, 'next_offset': next_offset, 'eof': eof})
        if operation == 'append':
            payload = base64.b64decode(content_b64.encode('ascii'))
            if len(payload) > max_bytes:
                fail('too_large')
            warnings = []
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1], create=True)
            fd = None
            existing_stat = None
            try:
                if expected_sha256:
                    try:
                        locked_fd, existing_stat = lock_existing(parent_fd, relative_parts[-1])
                    except FileNotFoundError:
                        fail('conflict', detail='checksum_mismatch')
                    current = read_existing(
                        parent_fd, relative_parts[-1], max_bytes, expected_stat=existing_stat
                    )
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        fail('conflict', detail='checksum_mismatch')
                try:
                    if locked_fd is None:
                        existing_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existing_stat = None
                if existing_stat is not None:
                    if stat.S_ISLNK(existing_stat.st_mode):
                        fail('unsafe')
                    if stat.S_ISDIR(existing_stat.st_mode):
                        fail('is_directory')
                try:
                    fd = os.open(relative_parts[-1], os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=parent_fd)
                    opened_stat = os.fstat(fd)
                    if not stat.S_ISREG(opened_stat.st_mode):
                        fail('unsafe')
                    if opened_stat.st_size + len(payload) > max_bytes:
                        fail('too_large')
                    write_all(fd, payload)
                    os.fsync(fd)
                    written_stat = os.fstat(fd)
                    try:
                        fsync_directory(parent_fd)
                    except StorageError as exc:
                        warnings.append({'code': 'cleanup_failed', 'errno': exc.errno})
                except OSError as exc:
                    # Some Volume backends reject every in-place write mode
                    # (EPERM on O_APPEND opens, EBADF on O_RDWR writes):
                    # compose the final bytes and fall through to the
                    # 'write' branch, whose publish/replace machinery works.
                    if exc.errno not in (errno.EPERM, errno.EBADF):
                        raise
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        fd = None
                    if existing_stat is None:
                        raise
                    existing_data = read_existing(
                        parent_fd, relative_parts[-1], max_bytes, expected_stat=existing_stat
                    )
                    if existing_stat.st_size + len(payload) > max_bytes:
                        fail('too_large')
                    payload = existing_data + payload
                    content_b64 = base64.b64encode(payload).decode('ascii')
                    operation = 'write'
                    overwrite = True
            finally:
                if fd is not None:
                    os.close(fd)
                if locked_fd is not None and operation != 'write':
                    os.close(locked_fd)
                    locked_fd = None
                close_all(parent_fds)
            if operation == 'append':
                response = {'ok': True, 'entry': entry_for(written_stat, relative)}
                if warnings:
                    response['warnings'] = warnings
                respond(response)
        if operation == 'memory_migrate':
            if not relative_parts or len(relative_parts) != 1:
                fail('is_directory')
            memory_header_bytes = b'# Fleet Memory v2' + bytes([10])
            parent_fds, parent_fd = open_parent(root_fd, ['memory'], create=True)
            locked_fd, _lock_stat = lock_memory_change(parent_fd, relative)
            try:
                try:
                    legacy_stat = os.stat(relative_parts[-1], dir_fd=root_fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                        if stat.S_ISREG(target_stat.st_mode):
                            respond({'ok': True, 'entry': entry_for(target_stat, 'memory/' + relative_parts[-1])})
                    except FileNotFoundError:
                        pass
                    fail('not_found')
                if not stat.S_ISREG(legacy_stat.st_mode):
                    fail('unsafe')
                if legacy_stat.st_size > max_bytes - len(memory_header_bytes):
                    fail('too_large')
                body = read_existing(root_fd, relative_parts[-1], max_bytes, expected_stat=legacy_stat)
                try:
                    body.decode('utf-8')
                except UnicodeDecodeError:
                    fail('invalid_utf8')
                if body and not body.endswith(bytes([10])):
                    body += bytes([10])
                payload = memory_header_bytes + body
                try:
                    target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    target_stat = None
                if target_stat is not None:
                    if not stat.S_ISREG(target_stat.st_mode):
                        fail('unsafe')
                    current = read_existing(parent_fd, relative_parts[-1], max_bytes, expected_stat=target_stat)
                    if hashlib.sha256(current).hexdigest() != hashlib.sha256(payload).hexdigest():
                        # An operator-managed replacement store wins; leave the
                        # legacy root untouched rather than discarding evidence.
                        respond({'ok': True, 'entry': entry_for(target_stat, 'memory/' + relative_parts[-1])})
                    try:
                        os.unlink(relative_parts[-1], dir_fd=root_fd)
                        fsync_directory(root_fd)
                    except FileNotFoundError:
                        pass
                    except StorageError:
                        pass
                    respond({'ok': True, 'entry': entry_for(target_stat, 'memory/' + relative_parts[-1])})
                written_stat = publish_new(parent_fd, relative_parts[-1], payload)
                if type(written_stat) is tuple and len(written_stat) == 2:
                    written_stat, _cleanup_errno = written_stat
                try:
                    fsync_directory(parent_fd)
                except StorageError:
                    pass
                try:
                    os.unlink(relative_parts[-1], dir_fd=root_fd)
                    try:
                        fsync_directory(root_fd)
                    except StorageError:
                        pass
                except FileNotFoundError:
                    pass
                respond({'ok': True, 'entry': entry_for(written_stat, 'memory/' + relative_parts[-1])})
            finally:
                if locked_fd is not None:
                    os.close(locked_fd)
                    locked_fd = None
                close_all(parent_fds)
        if operation == 'memory_append':
            payload = base64.b64decode(content_b64.encode('ascii'))
            if len(payload) > max_bytes:
                fail('too_large')
            # This Volume backend forbids every in-place write mode on existing
            # files (O_APPEND opens fail EPERM; O_RDWR writes fail EBADF); only
            # temp-file publish and O_TRUNC fallback work. Appends therefore
            # compose the final bytes here and fall through to the 'write'
            # branch, whose publish/replace machinery is the proven path.
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1], create=True)
            locked_fd, _lock_stat = lock_memory_change(parent_fd, relative)
            try:
                try:
                    existing_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existing_stat = None
                if existing_stat is not None:
                    if not stat.S_ISREG(existing_stat.st_mode):
                        fail('unsafe')
                    if existing_stat.st_size + len(payload) > max_bytes:
                        fail('too_large')
                    # The stat->read gap is pinned by re-validating the inode
                    # identity over the read handle (stable on this backend).
                    read_fd = os.open(relative_parts[-1], os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=parent_fd)
                    try:
                        opened_stat = os.fstat(read_fd)
                        if (opened_stat.st_dev, opened_stat.st_ino) != (existing_stat.st_dev, existing_stat.st_ino):
                            fail('unsafe')
                        existing_data = b''
                        while len(existing_data) <= max_bytes:
                            chunk = os.read(read_fd, max_bytes + 1 - len(existing_data))
                            if not chunk:
                                break
                            existing_data += chunk
                        if len(existing_data) > max_bytes or len(existing_data) != existing_stat.st_size:
                            fail('unsafe')
                    finally:
                        os.close(read_fd)
                else:
                    existing_data = b''
                if operation == 'memory_append':
                    candidate = memory_record.fullmatch(payload.decode('utf-8'))
                    if candidate is None:
                        fail('cursor')
                    candidate_id = effective_memory_id(candidate)
                    append_memory_id = candidate_id
                    if existing_stat is None:
                        payload = b'# Fleet Memory v2\n' + payload
                    else:
                        try:
                            check_memory_log_encoding(existing_data)
                        except StorageError:
                            fail('unsafe')
                        record_ordinal = 0
                        active_ids = set()
                        for line in existing_data.decode('utf-8').splitlines(keepends=True):
                            existing = memory_record.fullmatch(line)
                            if existing is None:
                                continue
                            existing_id = effective_memory_id(existing, record_ordinal)
                            if (existing.group('timestamp'), existing.group('category'), existing.group('learning')) == (candidate.group('timestamp'), candidate.group('category'), candidate.group('learning')):
                                response = {'ok': True, 'entry': entry_for(existing_stat, relative),
                                    'memory_id': existing_id}
                                respond(response)
                            if existing_id == candidate_id:
                                fail('conflict', detail='memory_id_collision')
                            existing_supersedes_id = existing.group('supersedes_id')
                            if existing_supersedes_id is not None:
                                if existing_supersedes_id not in active_ids:
                                    fail('unsafe')
                                active_ids.remove(existing_supersedes_id)
                            active_ids.add(existing_id)
                            record_ordinal += 1
                        if existing_data and not existing_data.endswith(bytes([10])):
                            fail('unsafe')
                        candidate_supersedes_id = candidate.group('supersedes_id')
                        if candidate_supersedes_id is not None and candidate_supersedes_id not in active_ids:
                            fail('conflict', detail='supersedes_not_active')
                payload = existing_data + payload
                if len(payload) > max_bytes:
                    fail('too_large')
                content_b64 = base64.b64encode(payload).decode('ascii')
                operation = 'write'
                overwrite = existing_stat is not None
            finally:
                close_all(parent_fds)
        if operation in ('memory_edit', 'memory_delete'):
            if not relative_parts:
                fail('is_directory')
            if re.fullmatch(r'[0-9a-f]{8}', memory_id) is None:
                fail('cursor')
            replacement = None
            if operation == 'memory_edit':
                try:
                    update = json.loads(base64.b64decode(content_b64.encode('ascii')).decode('utf-8'))
                except (UnicodeDecodeError, ValueError):
                    fail('cursor')
                learning = update.get('learning') if isinstance(update, dict) else None
                requested_category = update.get('category') if isinstance(update, dict) else None
                updated_at = update.get('updated_at') if isinstance(update, dict) else None
                try:
                    updated_text = str(updated_at)
                    datetime.datetime.strptime(updated_text, '%Y-%m-%dT%H:%M:%SZ')
                except (TypeError, ValueError):
                    fail('cursor')
                if not isinstance(learning, str) or requested_category is not None and not isinstance(requested_category, str):
                    fail('cursor')
            fallback_overwrite = False
            warnings = []
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            locked_fd, _lock_stat = lock_memory_change(parent_fd, relative)
            try:
                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(target_stat.st_mode):
                    fail('unsafe')
                if target_stat.st_size > max_bytes:
                    fail('too_large')
                source = read_existing(parent_fd, relative_parts[-1], max_bytes, expected_stat=target_stat)
                if len(source) != target_stat.st_size:
                    fail('unsafe')
                try:
                    source_text = source.decode('utf-8')
                except UnicodeDecodeError:
                    fail('invalid_utf8')
                lines = source_text.splitlines(keepends=True)
                targets = []
                record_ordinal = 0
                for index, line in enumerate(lines):
                    match = memory_record.fullmatch(line)
                    if match is not None:
                        if effective_memory_id(match, record_ordinal) == memory_id:
                            targets.append((index, match))
                        record_ordinal += 1
                if not targets:
                    fail('not_found')
                if len(targets) != 1:
                    fail('conflict')
                target, target_match = targets[0]
                if operation == 'memory_edit':
                    category = requested_category if requested_category is not None else target_match.group('category')
                    source = target_match.group('source') or 'legacy_unknown'
                    supersedes = target_match.group('supersedes_id')
                    supersession = f' supersedes:{supersedes}' if supersedes is not None else ''
                    replacement = (
                        f"- [{target_match.group('timestamp')}] **{category}** <!-- id:{memory_id} source:{source} updated:{updated_text}{supersession} -->: {learning}\n"
                    )
                    if len(replacement.encode('utf-8')) > 4096:
                        fail('invalid_record')
                    lines[target] = replacement
                else:
                    del lines[target]
                payload = ''.join(lines).encode('utf-8')
                if len(payload) > max_bytes:
                    fail('too_large')
                try:
                    written_stat = replace_existing(parent_fd, relative_parts[-1], payload)
                except ReplacementUnsupported:
                    previous = read_existing(
                        parent_fd, relative_parts[-1], max_bytes, expected_stat=target_stat
                    )
                    try:
                        written_stat = overwrite_existing_direct(parent_fd, relative_parts[-1], payload, previous)
                    except StorageError as direct_exc:
                        if direct_exc.errno not in _WORM_RECREATE_ERRNOS:
                            raise
                        written_stat = recreate_existing(parent_fd, relative_parts[-1], payload, previous)
                    fallback_overwrite = True
            finally:
                if locked_fd is not None:
                    os.close(locked_fd)
                    locked_fd = None
                close_all(parent_fds)
            cleanup_errno = None
            if fallback_overwrite:
                warnings.append({'code': 'non_atomic_overwrite'})
            if type(written_stat) is tuple and len(written_stat) == 2:
                written_stat, cleanup_errno = written_stat
            response = {'ok': True, 'entry': entry_for(written_stat, relative)}
            if replacement is not None:
                response['record'] = replacement
            if cleanup_errno is not None:
                warnings.append({'code': 'cleanup_failed', 'errno': cleanup_errno})
            if warnings:
                response['warnings'] = warnings
            respond(response)
        if operation == 'unlink':
            if not relative_parts:
                fail('is_directory')
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            try:
                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(target_stat.st_mode):
                    fail('unsafe')
                os.unlink(relative_parts[-1], dir_fd=parent_fd)
                warnings = []
                try:
                    fsync_directory(parent_fd)
                except StorageError as exc:
                    warnings.append({'code': 'cleanup_failed', 'errno': exc.errno})
                response = {'ok': True}
                if warnings:
                    response['warnings'] = warnings
                respond(response)
            finally:
                close_all(parent_fds)
        if operation == 'delete':
            # Regular files and EMPTY directories only. Symlinks, FIFOs, and
            # other non-regular nodes fail
            # closed over the stat result and are never opened; there is no
            # force flag, so non-empty directories fail with 'conflict'.
            if not relative_parts:
                fail('unsafe')
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            try:
                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(target_stat.st_mode):
                    fail('unsafe')
                if stat.S_ISREG(target_stat.st_mode):
                    if expected_sha256:
                        if target_stat.st_size > max_bytes:
                            fail('read_bound')
                        # Hold the mounted-agent revision fence across compare,
                        # pathname identity revalidation, and unlink. Other
                        # generated sandbox operations honor the same advisory
                        # lock, so a stale checksum cannot delete a newer CAS
                        # revision.
                        locked_fd, target_stat = lock_existing(parent_fd, relative_parts[-1])
                        source = read_existing(
                            parent_fd, relative_parts[-1], max_bytes, expected_stat=target_stat
                        )
                        if len(source) != target_stat.st_size:
                            fail('unsafe')
                        if hashlib.sha256(source).hexdigest() != expected_sha256:
                            fail('conflict', detail='checksum_mismatch')
                        current_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                        if (
                            not stat.S_ISREG(current_stat.st_mode)
                            or (current_stat.st_dev, current_stat.st_ino) != (target_stat.st_dev, target_stat.st_ino)
                            or current_stat.st_size != target_stat.st_size
                        ):
                            fail('conflict', detail='checksum_mismatch')
                    os.unlink(relative_parts[-1], dir_fd=parent_fd)
                elif stat.S_ISDIR(target_stat.st_mode):
                    if expected_sha256:
                        fail('conflict', detail='checksum_mismatch')
                    try:
                        os.rmdir(relative_parts[-1], dir_fd=parent_fd)
                    except OSError as exc:
                        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                            fail('conflict', detail='not_empty')
                        raise
                else:
                    fail('unsafe')
                warnings = []
                try:
                    fsync_directory(parent_fd)
                except StorageError as exc:
                    warnings.append({'code': 'cleanup_failed', 'errno': exc.errno})
                response = {'ok': True}
                if warnings:
                    response['warnings'] = warnings
                respond(response)
            finally:
                if locked_fd is not None:
                    os.close(locked_fd)
                    locked_fd = None
                close_all(parent_fds)
        if operation == 'patch':
            # Bounded unique find-replace for one regular UTF-8 file. The
            # identity-pinned read (O_RDONLY + fstat dev/ino/size match)
            # guards the stat->read gap; the patched bytes then compose and
            # fall through to the 'write' branch whose temp/publish machinery
            # (with the O_TRUNC overwrite fallback) performs the mutation.
            if not relative_parts:
                fail('is_directory')
            try:
                update = json.loads(base64.b64decode(content_b64.encode('ascii')).decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                fail('cursor')
            old = update.get('old') if isinstance(update, dict) else None
            new = update.get('new') if isinstance(update, dict) else None
            if not isinstance(old, str) or not old or not isinstance(new, str):
                fail('cursor')
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1])
            try:
                target_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(target_stat.st_mode):
                    fail('unsafe')
                if stat.S_ISDIR(target_stat.st_mode):
                    fail('is_directory')
                # FIFOs and other non-regular nodes are refused over the stat
                # result and never opened, so a FIFO cannot block the agent.
                if not stat.S_ISREG(target_stat.st_mode):
                    fail('unsafe')
                if target_stat.st_size > max_bytes:
                    fail('too_large')
                source = read_existing(parent_fd, relative_parts[-1], max_bytes, expected_stat=target_stat)
                if len(source) != target_stat.st_size:
                    fail('unsafe')
                if expected_sha256 and hashlib.sha256(source).hexdigest() != expected_sha256:
                    fail('conflict', detail='checksum_mismatch')
                try:
                    source_text = source.decode('utf-8')
                except UnicodeDecodeError:
                    fail('invalid_utf8')
                occurrences = source_text.count(old)
                if occurrences < 1:
                    fail('conflict', detail='missing')
                if occurrences > 1:
                    fail('conflict', detail='ambiguous')
                try:
                    payload = source_text.replace(old, new, 1).encode('utf-8')
                except UnicodeEncodeError:
                    fail('cursor')
                if len(payload) > max_bytes:
                    fail('too_large')
                content_b64 = base64.b64encode(payload).decode('ascii')
                operation = 'write'
                overwrite = True
                checksum = True
            finally:
                close_all(parent_fds)
        if operation == 'write':
            payload = base64.b64decode(content_b64.encode('ascii'))
            if len(payload) > max_bytes:
                fail('too_large')
            fallback_overwrite = False
            warnings = []
            parent_fds, parent_fd = open_parent(root_fd, relative_parts[:-1], create=True)
            if expected_sha256 and locked_fd is None:
                try:
                    locked_fd, existing_stat = lock_existing(parent_fd, relative_parts[-1])
                except FileNotFoundError:
                    fail('conflict', detail='checksum_mismatch')
                current = read_existing(
                    parent_fd, relative_parts[-1], max_bytes, expected_stat=existing_stat
                )
                if hashlib.sha256(current).hexdigest() != expected_sha256:
                    fail('conflict', detail='checksum_mismatch')
            try:
                if locked_fd is None:
                    existing_stat = None
                    try:
                        existing_stat = os.stat(relative_parts[-1], dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        existing_stat = None
                if existing_stat is not None:
                    if stat.S_ISLNK(existing_stat.st_mode):
                        fail('unsafe')
                    if stat.S_ISDIR(existing_stat.st_mode):
                        fail('is_directory')
                    if not overwrite:
                        fail('conflict')
                    try:
                        written_stat = replace_existing(parent_fd, relative_parts[-1], payload)
                    except ReplacementUnsupported:
                        previous = read_existing(
                            parent_fd, relative_parts[-1], max_bytes, expected_stat=existing_stat
                        )
                        try:
                            written_stat = overwrite_existing_direct(parent_fd, relative_parts[-1], payload, previous)
                        except StorageError as direct_exc:
                            if direct_exc.errno not in _WORM_RECREATE_ERRNOS:
                                raise
                            written_stat = recreate_existing(parent_fd, relative_parts[-1], payload, previous)
                        fallback_overwrite = True
                else:
                    written_stat = publish_new(parent_fd, relative_parts[-1], payload)
            finally:
                close_all(parent_fds)
            cleanup_errno = None
            if fallback_overwrite:
                warnings.append({'code': 'non_atomic_overwrite'})
            if type(written_stat) is tuple and len(written_stat) == 2:
                written_stat, cleanup_errno = written_stat
            entry = entry_for(written_stat, relative)
            # Opt-in response checksum (``patch`` sets it before falling
            # through): hash the exact bytes just published so REST clients
            # can chain content preconditions without an extra round trip.
            if checksum and stat.S_ISREG(written_stat.st_mode):
                entry['checksum'] = hashlib.sha256(payload).hexdigest()
            response = {'ok': True, 'entry': entry}
            if append_memory_id:
                response['memory_id'] = append_memory_id
            if cleanup_errno is not None:
                warnings.append({'code': 'cleanup_failed', 'errno': cleanup_errno})
            if warnings:
                response['warnings'] = warnings
            respond(response)
        fail('unsupported')
    except _AgentResponse as response:
        return response.payload
    except FileNotFoundError:
        return {'ok': False, 'error': 'not_found'}
    except FileExistsError:
        return {'ok': False, 'error': 'conflict'}
    except IsADirectoryError:
        return {'ok': False, 'error': 'is_directory'}
    except NotADirectoryError:
        return {'ok': False, 'error': 'not_directory'}
    except UnsafePath:
        return {'ok': False, 'error': 'unsafe'}
    except StorageError as exc:
        return {'ok': False, 'error': 'unsupported_storage', 'errno': exc.errno}
    except OSError:
        return {'ok': False, 'error': 'unsafe'}
    finally:
        if locked_fd is not None:
            try:
                os.close(locked_fd)
            except OSError:
                pass
        close_all(base_fds)
