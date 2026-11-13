"""Session IDs, workspace-relative paths, bounded dataset handles, and the workspace API installed in remote Python."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from dspy import SandboxSerializable

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_WORKSPACE_ROOTS = frozenset({"files", "artifacts", "memory", "skills", "buffers", "sessions"})
# The single byte ceiling for one workspace file: the host API, the remote Workspace, and settings
# all enforce the same number, so a value raised here cannot silently disagree with the sandbox.
MAX_WORKSPACE_FILE_BYTES = 25_000_000

_WORKSPACE_FILES_CODE = (
    "import json,os\n"
    "root='/workspace'\n"
    "items=[]\n"
    f"for area in {tuple(sorted(_ALLOWED_WORKSPACE_ROOTS))!r}:\n"
    " managed=os.path.join(root,area)\n"
    " if os.path.islink(managed): raise ValueError('workspace root may not be a symbolic link')\n"
    " for base,dirs,files in os.walk(managed,followlinks=False):\n"
    "  dirs[:]=[name for name in dirs if not os.path.islink(os.path.join(base,name))]\n"
    "  for name in files:\n"
    "   path=os.path.join(base,name)\n"
    "   if not os.path.islink(path) and os.path.isfile(path): items.append(os.path.relpath(path,root))\n"
)

_DIGESTS_CODE = (
    _WORKSPACE_FILES_CODE + "import hashlib\n"
    "digests={}\n"
    "sizes={}\n"
    "for relative in items:\n"
    " sizes[relative]=os.path.getsize(os.path.join(root,relative))\n"
    " digest=hashlib.sha256()\n"
    " with open(os.path.join(root,relative),'rb') as handle:\n"
    "  for block in iter(lambda: handle.read(1048576), b''): digest.update(block)\n"
    " digests[relative]=digest.hexdigest()\n"
)

ENVIRONMENT_MODULES = (
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("scipy", "scipy"),
    ("mpmath", "mpmath"),
    ("sympy", "sympy"),
    ("gmpy2", "gmpy2"),
    ("polars", "polars"),
    ("pyarrow", "pyarrow"),
    ("sklearn", "scikit-learn"),
    ("openpyxl", "openpyxl"),
    ("matplotlib", "matplotlib"),
    ("PIL", "pillow"),
    ("pypdf", "pypdf"),
    ("requests", "requests"),
    ("bs4", "beautifulsoup4"),
    ("pdfplumber", "pdfplumber"),
    ("reportlab", "reportlab"),
    ("docx", "python-docx"),
    ("pptx", "python-pptx"),
    ("markitdown", "markitdown"),
    ("dspy", "dspy"),
    ("gepa", "gepa"),
)

ENVIRONMENT_CODE = (
    "import importlib.metadata,importlib.util,platform,shutil,sys\n"
    "found=[]\n"
    f"for module,distribution in {ENVIRONMENT_MODULES!r}:\n"
    " if importlib.util.find_spec(module) is None: continue\n"
    " try: found.append(module+' '+importlib.metadata.version(distribution))\n"
    " except importlib.metadata.PackageNotFoundError: found.append(module)\n"
    "names=[item.split()[0] for item in found]\n"
    f"missing=[module for module,_ in {ENVIRONMENT_MODULES!r} if module not in names]\n"
    "environment='Sandbox environment: Python '+platform.python_version()+'; installed: '+(', '.join(found) or 'none')"
    "+'; not installed: '+(', '.join(missing) or 'none')"
    "+'; coding tools: '+(', '.join(name for name in "
    "('git','node','npm','pnpm','tsc','uv','pytest','ruff','rg','jq') if shutil.which(name)) or 'none')"
    "+('; call sys.set_int_max_str_digits(0) before converting integers over 4300 digits to str'"
    " if hasattr(sys,'set_int_max_str_digits') else '')+'.'\n"
)


def turn_inputs_code() -> str:
    """Build the single remote exec script that gathers workspace digests and environment info."""
    return (
        _DIGESTS_CODE
        + ENVIRONMENT_CODE
        + "print(json.dumps({'digests': digests, 'sizes': sizes, 'environment': environment, "
        "'missing': missing}, sort_keys=True))"
    )


def list_workspace_files_code() -> str:
    """Build the remote Python script to list regular files under managed workspace roots."""
    return _WORKSPACE_FILES_CODE + "print(json.dumps(sorted(items)))"


def workspace_files_metadata_code(
    rel_paths: list[str],
    *,
    create_parents: bool,
    max_bytes: int | None = None,
    include_digest: bool = False,
) -> str:
    """Build the remote Python script to validate paths, optionally create parent directories, and stat/digest files."""
    paths_b64 = base64.b64encode(json.dumps(rel_paths).encode("utf-8")).decode("ascii")
    create_clause = " os.makedirs(parent,exist_ok=True)\n" if create_parents else ""
    read_clause = " if not os.path.isfile(target): raise FileNotFoundError('workspace file is unavailable')\n"
    if create_parents:
        read_clause = (
            " if os.path.exists(target) and not os.path.isfile(target): "
            "raise ValueError('workspace target is not a file')\n"
        )
    size_clause = " size=os.path.getsize(target) if os.path.isfile(target) else None\n"
    if max_bytes is not None:
        size_clause += f" if size>{max_bytes}: raise ValueError('workspace file exceeds size limit')\n"
    result_clause = " return {'size':size}\n"
    if include_digest:
        result_clause = (
            " digest=hashlib.sha256()\n"
            " count=0\n"
            " with open(target,'rb') as handle:\n"
            "  before=os.fstat(handle.fileno())\n"
            "  for block in iter(lambda: handle.read(1048576),b''):\n"
            "   count+=len(block)\n"
            "   if count>size: raise ValueError('workspace file changed while inspected')\n"
            "   digest.update(block)\n"
            "  after=os.fstat(handle.fileno())\n"
            " if count!=size or before.st_size!=size or "
            "(before.st_mtime_ns,before.st_ctime_ns)!=(after.st_mtime_ns,after.st_ctime_ns):\n"
            "  raise ValueError('workspace file changed while inspected')\n"
            " return {'size':size,'sha256':digest.hexdigest()}\n"
        )
    return (
        "import base64,hashlib,json,os\n"
        "root=os.path.realpath('/workspace')\n"
        "def check(relative):\n"
        " parts=relative.split('/')\n"
        " parent=root\n"
        " for part in parts[:-1]:\n"
        "  parent=os.path.join(parent,part)\n"
        "  if os.path.islink(parent): raise ValueError('workspace path may not traverse symbolic links')\n"
        "  if os.path.exists(parent) and not os.path.isdir(parent): "
        "raise ValueError('workspace parent is not a directory')\n"
        " if os.path.commonpath((root,os.path.realpath(parent))) != root: "
        "raise ValueError('invalid workspace path')\n" + create_clause + " target=os.path.join(parent,parts[-1])\n"
        " if os.path.islink(target): raise ValueError('workspace target may not be a symbolic link')\n"
        " if os.path.commonpath((root,parent)) != root: raise ValueError('invalid workspace path')\n"
        + read_clause
        + size_clause
        + result_clause
        + "relatives=json.loads(base64.b64decode('"
        + paths_b64
        + "').decode('utf-8'))\n"
        "print(json.dumps([check(relative) for relative in relatives]))"
    )


def verify_python_artifact_code(path: str, *, digest: str, timeout: int) -> str:
    """Build the remote Python script to execute a verified `.py` artifact in an isolated process group."""
    return (
        "import hashlib,json,os,signal,subprocess,sys,tempfile\n"
        f"path={json.dumps('/workspace/' + path)}\n"
        f"assert hashlib.sha256(open(path,'rb').read()).hexdigest()=={digest!r}\n"
        "with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:\n"
        " p=subprocess.Popen([sys.executable,'-I',path],cwd='/workspace',stdout=out,stderr=err,"
        "start_new_session=True)\n"
        " complete=True\n"
        f" try: p.wait(timeout={timeout})\n"
        " except subprocess.TimeoutExpired: complete=False\n"
        " finally:\n"
        "  try: os.killpg(p.pid,signal.SIGKILL)\n"
        "  except ProcessLookupError: pass\n"
        "  p.wait()\n"
        " out.seek(0); data=out.read(65537)\n"
        " print(json.dumps({'complete':complete and len(data)<=65536,'exit_code':p.returncode,"
        "'stdout':data[:65536].decode('utf-8',errors='replace')}))\n"
    )


def validate_session_id(session_id: str) -> str:
    """Validate a session key before it is used as a Daytona Volume subpath."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1-64 letters, digits, dots, underscores, or hyphens")
    if session_id in {".", ".."} or ".." in session_id:
        raise ValueError("session_id must not contain parent path syntax")
    return session_id


def session_volume_subpath(session_id: str) -> str:
    """Return the provider-enforced private subtree for one Session."""
    return f"sessions/{validate_session_id(session_id)}"


def _child_input_path(path: str) -> str:
    """Validate a child input path, accepting the sandbox's absolute `/workspace/` form the model sees."""
    if isinstance(path, str) and path.startswith("/workspace/"):
        path = path[len("/workspace/") :]
    clean_path = _workspace_relative_path(path)
    if not clean_path.startswith(("files/", "skills/", "artifacts/")):
        raise ValueError("Child inputs must be files beneath files/, skills/ or artifacts/")
    return clean_path


def _workspace_relative_path(relative_path: str) -> str:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 1024
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(ord(character) < 32 for character in relative_path)
    ):
        raise ValueError("Workspace path must be a non-empty POSIX-relative path")
    parts = relative_path.split("/")
    if parts[0] not in _ALLOWED_WORKSPACE_ROOTS or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path is outside the allowed files, artifacts, memory, and skills roots")
    return "/".join(parts)


# This source is installed in the remote daemon, never executed on the host.
WORKSPACE_CODE = r"""
import difflib, hashlib, json, os
from pathlib import Path

class Workspace:
    roots = __FLEET_WORKSPACE_ROOTS__

    def resolve(self, relative):
        if not isinstance(relative, str) or not relative or '\\' in relative or len(relative)>1024:
            raise ValueError('invalid workspace path')
        parts = relative.split('/')
        if parts[0] not in self.roots or any(p in {'', '.', '..'} for p in parts):
            raise ValueError('invalid workspace path')
        if any(ord(c)<32 for c in relative):
            raise ValueError('invalid workspace path')
        root = Path('/workspace').resolve()
        target = root
        for part in parts:
            target = target / part
            if target.is_symlink(): raise ValueError('symbolic links are not allowed')
        if os.path.commonpath((str(root), str(target.resolve()))) != str(root):
            raise ValueError('workspace escape')
        if target.exists() and not target.is_file(): raise ValueError('not a regular file')
        return target

    def tree(self):
        out = []
        root = Path('/workspace')
        for area in sorted(self.roots):
            for base, dirs, names in os.walk(root / area, followlinks=False):
                dirs[:] = [n for n in dirs if not (Path(base)/n).is_symlink()]
                for name in sorted(names):
                    p = Path(base)/name
                    if p.is_file() and not p.is_symlink(): out.append(str(p.relative_to(root)))
                    if len(out)>5000: raise ValueError('too many workspace files')
        return sorted(out)

    def stat(self, path):
        p = self.resolve(path)
        h = hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda:f.read(1048576), b''): h.update(chunk)
        return {'path':path, 'size_bytes':p.stat().st_size, 'sha256':h.hexdigest()}

    def read(self, path, max_bytes=100000, offset=0):
        if not 0<=max_bytes<=__FLEET_MAX_FILE_BYTES__ or offset<0: raise ValueError('invalid read bounds')
        with self.resolve(path).open('rb') as f:
            f.seek(offset)
            return f.read(max_bytes)

    def read_text(self, path, max_bytes=100000, offset=0):
        return self.read(path,max_bytes,offset).decode('utf-8')

    def write(self, path, content, append=False):
        raw = content.encode() if isinstance(content,str) else content
        if not isinstance(raw,bytes) or len(raw)>__FLEET_MAX_FILE_BYTES__: raise ValueError('invalid content size')
        p = self.resolve(path)
        if append and p.exists() and p.stat().st_size+len(raw)>__FLEET_MAX_FILE_BYTES__: raise ValueError('file quota')
        p.parent.mkdir(parents=True,exist_ok=True)
        if append:
            with p.open('ab') as f: f.write(raw)
        else:
            import tempfile
            fd, temp = tempfile.mkstemp(dir=p.parent)
            try:
                with os.fdopen(fd,'wb') as f: f.write(raw)
                try:
                    os.replace(temp,p)
                except OSError as exc:
                    import errno
                    if exc.errno not in {errno.ENOSYS,errno.EXDEV,errno.EOPNOTSUPP}: raise
                    # Daytona Volume FUSE may not support rename. The owning
                    # session serializes writes; Postgres is the durable commit.
                    with p.open('wb') as f: f.write(raw)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        return str(p)

    def append(self,path,content): return self.write(path,content,append=True)
    def delete(self,path): self.resolve(path).unlink()
    def write_artifact(self,name,content): return self.write('artifacts/'+name,content)
    def remember(self,name,content): return self.write('memory/'+name,content)
    def diff(self,path,proposed):
        return ''.join(difflib.unified_diff(self.read_text(path).splitlines(True),proposed.splitlines(True)))
    def load_image(self,path,max_bytes=10000000):
        import base64, mimetypes
        import dspy
        if not 0<=max_bytes<=__FLEET_MAX_FILE_BYTES__: raise ValueError('invalid read bounds')
        p = self.resolve(path)
        size = p.stat().st_size
        if size>max_bytes: raise ValueError('image exceeds max_bytes')
        raw = p.read_bytes()
        mime, _ = mimetypes.guess_type(p.name)
        if not mime or not mime.startswith('image/'):
            if raw.startswith(b'\x89PNG\r\n\x1a\n'): mime = 'image/png'
            elif raw.startswith(b'\xff\xd8\xff'): mime = 'image/jpeg'
            elif raw.startswith((b'GIF87a', b'GIF89a')): mime = 'image/gif'
            elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP': mime = 'image/webp'
            else: raise ValueError('unsupported image format')
        return dspy.Image(url=f'data:{mime};base64,{base64.b64encode(raw).decode("ascii")}')

class WorkspaceDatasetView(dict):
    def __init__(self, metadata):
        # The host's one-way SandboxSerializable hook creates this guest-only view.
        # When forwarded to a nested agent or host tool, only JSON metadata crosses.
        super().__init__(metadata)
        self.metadata = self
        self.path = str(workspace.resolve(metadata['path']))
        actual = workspace.stat(metadata['path'])
        if metadata.get('sha256') and actual['sha256']!=metadata['sha256']:
            raise ValueError('dataset source changed')
    def preview(self,max_bytes=2000): return workspace.read_text(self.metadata['path'],max_bytes)
    def chunks(self,chunk_bytes=1048576):
        if not 1<=chunk_bytes<=1048576: raise ValueError('invalid chunk size')
        with open(self.path,'rb') as f:
            for chunk in iter(lambda:f.read(chunk_bytes),b''): yield chunk
    def lines(self):
        with open(self.path,encoding='utf-8') as f:
            for line in f:
                if len(line)>1000000: raise ValueError('line exceeds limit; use chunks')
                yield line
    def search(self,query,limit=20):
        if not 1<=limit<=1000: raise ValueError('invalid search limit')
        out=[]
        for n,line in enumerate(self.lines(),1):
            if query in line:
                out.append((n,line[:2000]))
                if len(out)>=limit: break
        return out
    def rlm_preview(self,max_chars=500):
        return (
            f"WorkspaceDataset({self.metadata['path']!r}, {self.metadata.get('size_bytes', 0)} bytes): "
            f"{self.metadata.get('description', '')}. Use .path, .preview(), .chunks(), .lines(), .search()."
        )[:max_chars]
    def __str__(self): return self.rlm_preview()

workspace = Workspace()
""".replace("__FLEET_WORKSPACE_ROOTS__", repr(tuple(sorted(_ALLOWED_WORKSPACE_ROOTS)))).replace(
    "__FLEET_MAX_FILE_BYTES__", str(MAX_WORKSPACE_FILE_BYTES)
)


@dataclass(frozen=True)
class WorkspaceDataset(SandboxSerializable):
    """A content-addressed file handle injected once per native RLM or Flex invocation."""

    workspace_path: str
    description: str = ""
    size_bytes: int = 0
    sha256: str = ""

    def __post_init__(self) -> None:
        """Normalize workspace_path to a workspace-relative path and validate the metadata bounds."""
        path = self.workspace_path.removeprefix("/workspace/")
        object.__setattr__(self, "workspace_path", _workspace_relative_path(path))
        if self.size_bytes < 0 or len(self.description) > 2000:
            raise ValueError("Invalid dataset metadata")

    def to_descriptor(self) -> dict[str, str | int]:
        """Persist the original four-field descriptor, independently of sandbox serialization."""
        return {
            "workspace_path": self.workspace_path,
            "description": self.description,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    def sandbox_setup(self) -> str:
        """Import JSON; Fleet's interpreter preamble already installs WorkspaceDatasetView."""
        return "import json"

    def to_sandbox(self) -> bytes:
        """Serialize metadata as UTF-8 bytes; the source file stays in the workspace."""
        return json.dumps(
            {
                "path": self.workspace_path,
                "description": self.description,
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            }
        ).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        """Return sandbox code that binds var_name to a WorkspaceDatasetView built from data_expr."""
        return f"{var_name} = WorkspaceDatasetView(json.loads({data_expr}))"

    def rlm_preview(self, max_chars: int = 500) -> str:
        """Return a bounded one-line summary of this handle for the RLM prompt."""
        return (
            f"WorkspaceDataset({self.workspace_path!r}, {self.size_bytes} bytes): "
            f"{self.description}. Use .path, .preview(), .chunks(), .lines(), .search()."
        )[:max_chars]

    def __str__(self) -> str:
        """Return the same summary as rlm_preview."""
        return self.rlm_preview()
