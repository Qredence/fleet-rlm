"""Per-Turn workspace digests: a cached, progress-reporting hash of /workspace, run under a stall watch.

The digest walk runs as a detached background process in the sandbox. It reports progress (phase, files, bytes)
to a status file that the host polls with short, separate execs. The host declares the walk stuck only when that
progress stops changing for `stall_seconds`; a slow but advancing walk is never cut off. SHA-256 results are
cached in the sandbox, keyed by each file's stat identity, so unchanged files are not read again on later Turns.
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from fleet_rlm.config import DIGEST_STALL_SECONDS
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES

# Safety limits only: time is bounded by the stall watch, not by these.
DIGEST_MAX_FILES = 5_000
# A file too large to replay is not worth hashing either: a replay of it fails the snapshot limit first.
DIGEST_MAX_FILE_BYTES = MAX_REPLAY_FILE_BYTES
DIGEST_POLL_SECONDS = 2.0
DIGEST_LAUNCH_WAIT_SECONDS = 2.0  # a small workspace finishes inside the launch exec itself
_CONTROL_EXEC_TIMEOUT = 30  # bound for each tiny launch/poll/kill exec, never for the walk itself

# Outside /workspace, so the cache is never listed, digested, collected as an artifact, or replayed.
# The sandbox user's home, expanded remotely (`daytona` in the stock images, whatever the snapshot uses otherwise).
DIGEST_CACHE_PATH = "~/.cache/fleet-rlm/workspace-digests.json"
DIGEST_RUN_ROOT = "/tmp/fleet/digest-runs"
_CACHE_VERSION = 1
# Files modified this close to the walk are not cached: a write in the same timestamp tick could keep size and
# times identical ("racily clean", as in git's index).
_RACY_SECONDS = 2

# Defines a no-op `_fleet_progress` when a remote snippet runs outside the watched runner.
PROGRESS_FALLBACK_CODE = "try: _fleet_progress\nexcept NameError:\n def _fleet_progress(*_args,**_kwargs): pass\n"


class WorkspaceDigestStalledError(RuntimeError):
    """The sandbox digest walk made no progress for the stall window and was stopped."""


class WorkspaceDigestFailedError(RuntimeError):
    """The sandbox digest walk exited without producing a result."""


def digests_code(
    max_files: int = DIGEST_MAX_FILES,
    max_file_bytes: int = DIGEST_MAX_FILE_BYTES,
    cache_path: str = DIGEST_CACHE_PATH,
    racy_seconds: int = _RACY_SECONDS,
) -> str:
    """Bind `digests` and `digest_stats` for /workspace (symbolic links skipped, never traversed).

    `artifacts/` is walked first so the file cap never hides an artifact; the rest follows in sorted order.
    A file over `max_file_bytes` is recorded as `unhashed:size=<n>:mtime_ns=<ns>`. A cached SHA-256 is reused
    only when size, mtime_ns, ctime_ns and inode all still match (ctime cannot be set back by `os.utime`), and
    only files older than the racy window are cached. Progress goes to `_fleet_progress` when the watched runner
    defines it; the code also runs standalone.
    """
    return (
        PROGRESS_FALLBACK_CODE + "import hashlib,json,os,time\n"
        "digests={}\n"
        "digest_stats={'hashed':0,'cached':0,'unhashed':0,'truncated':False}\n"
        "_root='/workspace'\n"
        f"_cache_path=os.path.abspath(os.path.expanduser({cache_path!r}))\n"
        "_cache_dir=os.path.dirname(_cache_path)\n"
        f"_racy_ns=time.time_ns()-{int(racy_seconds)}*1000000000\n"
        "try:\n"
        " with open(_cache_path,encoding='utf-8') as _handle: _cache=json.load(_handle)\n"
        f" _current=_cache.get('version')=={_CACHE_VERSION} and _cache.get('root')==_root\n"
        " _entries=_cache['entries'] if _current else {}\n"
        " if not isinstance(_entries,dict): _entries={}\n"
        "except (OSError,ValueError,TypeError,KeyError,AttributeError):\n"
        " _entries={}\n"
        "_fresh={}\n"
        "_bytes=0\n"
        "_dirs=0\n"
        "def _walk(top,skip):\n"
        " global _dirs\n"
        " for base,dirs,files in os.walk(top,followlinks=False):\n"
        "  _dirs+=1\n"
        "  _fleet_progress('digests',len(digests),_bytes,dirs=_dirs)\n"
        "  dirs[:]=sorted(n for n in dirs if not os.path.islink(os.path.join(base,n))\n"
        "   and os.path.join(base,n)!=_cache_dir and not (base==_root and n in skip))\n"
        "  for name in sorted(files): yield os.path.join(base,name)\n"
        "def _paths():\n"
        " yield from _walk(os.path.join(_root,'artifacts'),())\n"
        " yield from _walk(_root,('artifacts',))\n"
        "_fleet_progress('digests',0,0,force=True)\n"
        "for path in _paths():\n"
        " if path==_cache_path or os.path.islink(path) or not os.path.isfile(path): continue\n"
        f" if len(digests)>={int(max_files)}:\n"
        "  digest_stats['truncated']=True\n"
        "  break\n"
        " rel=os.path.relpath(path,_root)\n"
        " try:\n"
        "  info=os.stat(path)\n"
        "  key=[info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_ino]\n"
        f"  if info.st_size>{int(max_file_bytes)}:\n"
        "   value='unhashed:size=%d:mtime_ns=%d'%(info.st_size,info.st_mtime_ns)\n"
        "   digest_stats['unhashed']+=1\n"
        "  else:\n"
        "   hit=_entries.get(rel)\n"
        "   if isinstance(hit,list) and len(hit)==5 and hit[:4]==key and isinstance(hit[4],str) and len(hit[4])==64:\n"
        "    value=hit[4]\n"
        "    digest_stats['cached']+=1\n"
        "   else:\n"
        "    digest=hashlib.sha256()\n"
        "    with open(path,'rb') as handle:\n"
        "     for block in iter(lambda: handle.read(1048576), b''):\n"
        "      digest.update(block)\n"
        "      _bytes+=len(block)\n"
        "      _fleet_progress('digests',len(digests),_bytes,dirs=_dirs)\n"
        "    value=digest.hexdigest()\n"
        "    digest_stats['hashed']+=1\n"
        "    after=os.stat(path)\n"
        "    if [after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_ino]!=key: key=None\n"
        "   if key is not None and max(key[1],key[2])<_racy_ns: _fresh[rel]=key+[value]\n"
        " except OSError: continue\n"
        " digests[rel]=value\n"
        " _fleet_progress('digests',len(digests),_bytes,dirs=_dirs)\n"
        "try:\n"
        " os.makedirs(_cache_dir,mode=0o700,exist_ok=True)\n"
        " _tmp='%s.%d.tmp'%(_cache_path,os.getpid())\n"
        " with open(os.open(_tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w',encoding='utf-8') as _handle:\n"
        f"  json.dump({{'version':{_CACHE_VERSION},'root':_root,'entries':_fresh}},_handle,separators=(',',':'))\n"
        " os.replace(_tmp,_cache_path)\n"
        "except OSError: pass\n"
        "_fleet_progress('after_digests',len(digests),_bytes,dirs=_dirs,force=True)\n"
    )


def _worker_source(code: str, run_dir: str) -> str:
    """The detached worker: `code` with `_fleet_progress` defined, writing `done` only after a clean finish."""
    return (
        "import json as _fj,os as _fo,sys as _fs,time as _ft\n"
        f"_FLEET_RUN={run_dir!r}\n"
        "_fleet_last=[0.0,None]\n"
        "def _fleet_progress(phase,files=0,nbytes=0,dirs=0,force=False):\n"
        " now=_ft.monotonic()\n"
        " if not force and _fleet_last[1]==phase and now-_fleet_last[0]<0.5: return\n"
        " _fleet_last[0],_fleet_last[1]=now,phase\n"
        " tmp=_fo.path.join(_FLEET_RUN,'status.json.tmp')\n"
        " with open(tmp,'w') as handle: _fj.dump({'phase':phase,'files':files,'bytes':nbytes,'dirs':dirs},handle)\n"
        " _fo.replace(tmp,_fo.path.join(_FLEET_RUN,'status.json'))\n"
        f"{code}\n"
        "_fs.stdout.flush()\n"
        "open(_fo.path.join(_FLEET_RUN,'done'),'w').close()\n"
    )


def _poll_code(run_dir: str) -> str:
    """Print one JSON status line for the run.

    A finished run is renamed to `<run>.done` / `<run>.failed` rather than deleted, so a retried poll gets the
    same answer; the next launch prunes old finished runs.
    """
    return (
        "import json,os\n"
        f"run={run_dir!r}\n"
        "def read(path,limit=None):\n"
        " try:\n"
        "  with open(path,encoding='utf-8',errors='replace') as handle:\n"
        "   text=handle.read()\n"
        "  return text[-limit:] if limit else text\n"
        " except OSError: return None\n"
        "def alive(pid):\n"
        " try:\n"
        "  with open('/proc/%d/stat'%pid) as handle: return handle.read().rsplit(')',1)[1].split()[0]!='Z'\n"
        " except OSError: pass\n"
        " try: os.kill(pid,0)\n"
        " except ProcessLookupError: return False\n"
        " except OSError: return True\n"
        " return not os.path.isdir('/proc')\n"
        "out={}\n"
        "if os.path.isdir(run) and os.path.exists(os.path.join(run,'done')):\n"
        " try: os.rename(run,run+'.done')\n"
        " except OSError: pass\n"
        "elif os.path.isdir(run):\n"
        " try: pid=int(read(os.path.join(run,'pid')) or 0)\n"
        " except ValueError: pid=0\n"
        " if pid>0 and alive(pid): out['state'],out['progress']='running',read(os.path.join(run,'status.json'))\n"
        " elif not os.path.exists(os.path.join(run,'done')):\n"
        "  try: os.rename(run,run+'.failed')\n"
        "  except OSError: pass\n"
        "if not out:\n"
        " if os.path.isdir(run+'.done'):\n"
        "  out['state'],out['output']='done',read(os.path.join(run+'.done','out')) or ''\n"
        " elif os.path.isdir(run+'.failed'):\n"
        "  out['state'],out['error']='failed',read(os.path.join(run+'.failed','err'),2000) or ''\n"
        " elif os.path.isdir(run): out['state'],out['progress']='running',read(os.path.join(run,'status.json'))\n"
        " else: out['state']='missing'\n"
        "print(json.dumps(out))\n"
    )


def _launch_code(worker: str, run_dir: str, wait_seconds: float) -> str:
    """Start the worker detached (own session, stdio to files) once per run directory, wait briefly, then poll."""
    encoded = base64.b64encode(worker.encode("utf-8")).decode("ascii")
    return (
        "import base64,os,shutil,subprocess,sys,time\n"
        f"run={run_dir!r}\n"
        "root=os.path.dirname(run)\n"
        "try:\n"  # prune finished runs after an hour and abandoned ones after a day
        " for name in os.listdir(root):\n"
        "  path=os.path.join(root,name)\n"
        "  age=time.time()-os.stat(path).st_mtime\n"
        "  if age>(3600 if name.endswith(('.done','.failed')) else 86400): shutil.rmtree(path,ignore_errors=True)\n"
        "except OSError: pass\n"
        # A retried launch never starts a second worker.
        "if not any(os.path.isdir(run+suffix) for suffix in ('','.done','.failed')):\n"
        " os.makedirs(run+'.tmp',mode=0o700,exist_ok=True)\n"
        " with open(os.path.join(run+'.tmp','worker.py'),'wb') as handle:\n"
        f"  handle.write(base64.b64decode({encoded!r}))\n"
        " os.rename(run+'.tmp',run)\n"
        " out=open(os.path.join(run,'out'),'wb')\n"
        " err=open(os.path.join(run,'err'),'wb')\n"
        " proc=subprocess.Popen([sys.executable,os.path.join(run,'worker.py')],stdin=subprocess.DEVNULL,\n"
        "  stdout=out,stderr=err,cwd=run,start_new_session=True)\n"
        " with open(os.path.join(run,'pid'),'w') as handle: handle.write(str(proc.pid))\n"
        " try: proc.wait(timeout=" + repr(float(wait_seconds)) + ")\n"
        " except subprocess.TimeoutExpired: pass\n" + _poll_code(run_dir)
    )


def _kill_code(run_dir: str) -> str:
    return (
        "import os,signal\n"
        f"run={run_dir!r}\n"
        "try:\n"
        " pid=int(open(os.path.join(run,'pid')).read())\n"
        " os.killpg(pid,signal.SIGKILL)\n"
        "except (OSError,ValueError): pass\n"
        "try: os.rename(run,run+'.failed')\n"
        "except OSError: pass\n"
        "print('killed')\n"
    )


def run_watched_python(
    exec_python: Callable[[str, int], str],
    code: str,
    *,
    stall_seconds: float = DIGEST_STALL_SECONDS,
    poll_seconds: float = DIGEST_POLL_SECONDS,
    launch_wait_seconds: float = DIGEST_LAUNCH_WAIT_SECONDS,
    run_root: str = DIGEST_RUN_ROOT,
    check: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run `code` as a detached sandbox process and return its stdout, failing only if it stops progressing.

    `exec_python(source, timeout)` runs a short Python program in the sandbox and returns its stdout; it is used
    for the launch, the polls, and a kill, each bounded by a small control timeout. There is no overall time
    limit: the walk is declared stuck (and killed) only when its reported progress has not changed for
    `stall_seconds`. `check` (e.g. the Turn's cancellation/deadline check) runs between polls; if it raises,
    the remote process is killed before the error propagates.
    """
    run_dir = f"{run_root.rstrip('/')}/{uuid.uuid4().hex}"
    worker = _worker_source(code, run_dir)
    last_progress: Any = None
    last_change = clock()

    def kill() -> None:
        # Best effort; the stall or cancellation is what callers need to see.
        with contextlib.suppress(Exception):
            exec_python(_kill_code(run_dir), _CONTROL_EXEC_TIMEOUT)

    status = _parse_status(exec_python(_launch_code(worker, run_dir, launch_wait_seconds), _CONTROL_EXEC_TIMEOUT))
    while True:
        state = status.get("state")
        if state == "done":
            output = status.get("output")
            return output if isinstance(output, str) else ""
        if state == "failed":
            raise WorkspaceDigestFailedError(f"Remote watched command failed: {str(status.get('error', ''))[-500:]}")
        if state != "running":
            raise WorkspaceDigestFailedError(f"Remote watched command is {state or 'unreadable'}")
        progress = status.get("progress")
        now = clock()
        if progress != last_progress:
            last_progress, last_change = progress, now
        elif now - last_change >= stall_seconds:
            kill()
            raise WorkspaceDigestStalledError(
                f"Remote watched command made no progress for {stall_seconds:g} s (last: {last_progress or 'none'})"
            )
        if check is not None:
            try:
                check()
            except BaseException:
                kill()
                raise
        sleep(poll_seconds)
        status = _parse_status(exec_python(_poll_code(run_dir), _CONTROL_EXEC_TIMEOUT))


def _parse_status(text: str) -> dict[str, Any]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise WorkspaceDigestFailedError("Unreadable status from the remote watched command")
    return value
