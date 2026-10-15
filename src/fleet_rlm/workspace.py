"""Bounded dataset handles and the workspace API installed in remote Python."""

from __future__ import annotations

import json
from dataclasses import dataclass

from dspy.primitives.sandbox_serializable import SandboxSerializable

# This source is installed in the remote daemon, never executed on the host.
WORKSPACE_CODE = r"""
import difflib, hashlib, json, os
from pathlib import Path

class Workspace:
    roots = {'files', 'artifacts', 'memory', 'skills', 'buffers', 'sessions'}

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
        if not 0<=max_bytes<=25000000 or offset<0: raise ValueError('invalid read bounds')
        with self.resolve(path).open('rb') as f:
            f.seek(offset)
            return f.read(max_bytes)

    def read_text(self, path, max_bytes=100000, offset=0):
        return self.read(path,max_bytes,offset).decode('utf-8')

    def write(self, path, content, append=False):
        raw = content.encode() if isinstance(content,str) else content
        if not isinstance(raw,bytes) or len(raw)>25000000: raise ValueError('invalid content size')
        p = self.resolve(path)
        if append and p.exists() and p.stat().st_size+len(raw)>25000000: raise ValueError('file quota')
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

class WorkspaceDatasetView:
    def __init__(self, metadata):
        self.metadata = metadata
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

workspace = Workspace()
"""


@dataclass(frozen=True)
class WorkspaceDataset(SandboxSerializable):
    """A content-addressed, bounded-preview handle to a session file."""

    workspace_path: str
    description: str = ""
    size_bytes: int = 0
    sha256: str = ""

    def __post_init__(self) -> None:
        from fleet_rlm.interpreter import _workspace_relative_path

        path = self.workspace_path.removeprefix("/workspace/")
        object.__setattr__(self, "workspace_path", _workspace_relative_path(path))
        if self.size_bytes < 0 or len(self.description) > 2000:
            raise ValueError("Invalid dataset metadata")

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        return json.dumps(
            {
                "path": self.workspace_path,
                "description": self.description,
                "size_bytes": self.size_bytes,
                "sha256": self.sha256,
            }
        ).encode()

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = WorkspaceDatasetView(json.loads({data_expr}))"

    def rlm_preview(self, max_chars: int = 500) -> str:
        return (
            f"WorkspaceDataset({self.workspace_path!r}, {self.size_bytes} bytes): "
            f"{self.description}. Use .path, .preview(), .chunks(), .lines(), .search()."
        )[:max_chars]

    def __str__(self) -> str:
        return self.rlm_preview()
