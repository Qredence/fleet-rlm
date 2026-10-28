"""Consolidated remote Python script builders for Daytona sandbox file, digest, note, and skill operations."""

from __future__ import annotations

import base64
import json
from typing import Any

from fleet_rlm.workspace import _ALLOWED_WORKSPACE_ROOTS

# Listing and input digests must describe the same managed workspace, not arbitrary scratch files.
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

# Remote code fragments; each binds one variable so a Turn can gather them in a single exec.
DIGESTS_CODE = (
    _WORKSPACE_FILES_CODE + "import hashlib\n"
    "digests={}\n"
    "for relative in items:\n"
    " digest=hashlib.sha256()\n"
    " with open(os.path.join(root,relative),'rb') as handle:\n"
    "  for block in iter(lambda: handle.read(1048576), b''): digest.update(block)\n"
    " digests[relative]=digest.hexdigest()\n"
)

# Probed rather than configured: the Turn snapshot and its package set are repository-owned
# (fleet_rlm.sandbox.snapshots); only optional packages can be absent at runtime.
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

CLEAR_WORKSPACE_CODE = (
    "import os,shutil\n"
    "for name in os.listdir('/workspace'):\n"
    " p=os.path.join('/workspace',name)\n"
    " try:\n"
    "  if os.path.islink(p) or not os.path.isdir(p): os.unlink(p)\n"
    "  else: shutil.rmtree(p)\n"
    " except FileNotFoundError: pass\n"
)


def notes_code(limit: int) -> str:
    """Bind `notes`: newest-first `{file, summary}` rows from the shared mount, empty when unreadable."""
    return (
        "import json, os\n"
        "notes = []\n"
        "try:\n"
        "    if os.path.ismount('/learned'):\n"
        "        names = [name for name in os.listdir('/learned') if name.endswith('.md')]\n"
        "        names.sort(key=lambda name: os.stat(os.path.join('/learned', name)).st_mtime, reverse=True)\n"
        f"        for name in names[:{limit}]:\n"
        "            with open(os.path.join('/learned', name), encoding='utf-8', errors='replace') as handle:\n"
        "                head = [handle.readline() for _ in range(8)]\n"
        "            lines = [line[len('summary: '):].strip() for line in head if line.startswith('summary: ')]\n"
        "            notes.append({'file': name, 'summary': (lines[0] if lines else '')[:120]})\n"
        "except OSError:\n"
        "    notes = None\n"
    )


def valid_notes(rows: Any) -> list[dict[str, str]]:
    """Filter raw note rows down to valid dictionary entries."""
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def turn_inputs_code(*, with_notes: bool, notes_limit: int) -> str:
    """Build the single remote exec script that gathers workspace digests, shared notes, and environment info."""
    return (
        DIGESTS_CODE
        + ENVIRONMENT_CODE
        + (notes_code(notes_limit) if with_notes else "notes = []\n")
        + "print(json.dumps({'digests': digests, 'notes': notes, "
        "'environment': environment, 'missing': missing}, sort_keys=True))"
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


def inspect_skills_code(rel_paths: list[str]) -> str:
    """Build the remote Python script to inspect existing workspace Skill file SHA-256 digests."""
    remote_manifest = json.dumps(rel_paths)
    return (
        "import hashlib,json,os\n"
        "root='/workspace'\n"
        f"paths=json.loads({remote_manifest!r})\n"
        "result={}\n"
        "for rel in paths:\n"
        " path=os.path.join(root,rel)\n"
        " current=root\n"
        " for part in rel.split('/'):\n"
        "  current=os.path.join(current,part)\n"
        "  if os.path.islink(current): raise ValueError('Existing Skill path traverses a symbolic link: '+rel)\n"
        " if os.path.isfile(path) and not os.path.islink(path):\n"
        "  with open(path,'rb') as handle: result[rel]=hashlib.sha256(handle.read()).hexdigest()\n"
        " elif os.path.lexists(path): raise ValueError('Existing Skill path is not a regular file: '+rel)\n"
        "print(json.dumps(result))"
    )


def export_skills_code() -> str:
    """Build the remote Python script to export runtime-authored Skills from `/workspace/skills`."""
    return (
        "import base64,json,os\n"
        "root='/workspace/skills'\n"
        "items=[]\n"
        "total=0\n"
        "for parent,dirs,files in os.walk(root, followlinks=False):\n"
        " dirs[:]=[d for d in dirs if not os.path.islink(os.path.join(parent,d))]\n"
        " for name in files:\n"
        "  path=os.path.join(parent,name)\n"
        "  if os.path.islink(path) or not os.path.isfile(path): continue\n"
        "  size=os.path.getsize(path)\n"
        "  if size>1000000: raise ValueError('Skill file exceeds the size limit')\n"
        "  with open(path,'rb') as handle: raw=handle.read(size+1)\n"
        "  if len(raw)!=size: raise ValueError('Skill changed during export')\n"
        "  total+=size\n"
        "  if total>5000000 or len(items)>=200: raise ValueError('Skill export exceeds "
        "the synchronization bounds')\n"
        "  items.append({'rel':os.path.relpath(path,root),'b64':base64.b64encode(raw).decode()})\n"
        "print(json.dumps(items))"
    )
