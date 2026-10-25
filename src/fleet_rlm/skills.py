"""Agent Skills discovery, validation, and per-session Volume synchronization."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import mlflow
import yaml
from mlflow.entities import SpanType

if TYPE_CHECKING:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

_LOGGER = logging.getLogger(__name__)
# Skills shipped with Fleet; a user Skill with the same name in the local folder replaces one.
BUNDLED_SKILLS_DIR = Path(__file__).parent / "bundled_skills"
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
MAX_SKILL_FILE_BYTES = 1_000_000
MAX_SKILL_FILES = 200
MAX_SKILL_TOTAL_BYTES = 5_000_000

SANDBOX_SKILL_HELPERS_CODE = r'''
import ast, json, os, re, subprocess
from pathlib import Path

SKILL_ROOT = Path("/workspace/skills")
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SKILL_BYTES = 1000000
_MAX_SKILL_FILES = 200

def _skill_dir(name):
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError("Invalid skill name")
    root = SKILL_ROOT.resolve()
    lexical_path = root / name
    if lexical_path.is_symlink():
        raise ValueError("Skill directories may not be symbolic links")
    path = lexical_path.resolve()
    if os.path.commonpath((str(root), str(path))) != str(root):
        raise ValueError("Skill path escapes the skill directory")
    return path

def _safe_resource_file(base, rel_path, folders):
    if not isinstance(rel_path, str) or not rel_path or "\\" in rel_path or "\x00" in rel_path:
        raise ValueError("Invalid Skill resource path")
    relative = Path(rel_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Invalid Skill resource path")
    target = base
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Skill resources may not traverse symbolic links")
    allowed = [base / folder for folder in folders]
    if not any(os.path.commonpath((str(root), str(target))) == str(root) for root in allowed):
        raise ValueError("Skill resource is outside its allowed folders")
    return target

def _parse_frontmatter(raw_text):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", raw_text, re.DOTALL)
    if not match:
        return {}, raw_text
    metadata = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        elif value.startswith("'"):
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                pass
        metadata[key.strip()] = value
    return metadata, match.group(2).strip()

def list_skills():
    """Return the compact Stage-1 skill catalog."""
    result = []
    if not SKILL_ROOT.is_dir():
        return result
    for skill_md in sorted(SKILL_ROOT.glob("*/SKILL.md")):
        folder = skill_md.parent.name
        try:
            if skill_md.parent.is_symlink() or skill_md.is_symlink() or skill_md.stat().st_size > _MAX_SKILL_BYTES:
                continue
            metadata, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            if metadata.get("name") != folder or not metadata.get("description"):
                continue
            result.append({"name": folder, "description": str(metadata["description"])})
        except (OSError, UnicodeError):
            continue
    return result

def use_skill(name):
    """Stage-2 activation: return one Skill's instructions and resource names."""
    base = _skill_dir(name)
    skill_md = base / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink() or skill_md.stat().st_size > _MAX_SKILL_BYTES:
        raise FileNotFoundError("Skill instructions are unavailable")
    metadata, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    if metadata.get("name") != name:
        raise ValueError("Skill metadata does not match its directory")
    resources = {}
    for folder in ("scripts", "references", "assets"):
        path = base / folder
        if path.is_symlink():
            raise ValueError("Skill resource directories may not be symbolic links")
        if path.is_dir():
            entries = []
            for parent, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = sorted(name for name in dirs if not os.path.islink(os.path.join(parent, name)))
                for filename in sorted(files):
                    candidate = Path(parent) / filename
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    if candidate.stat().st_size > _MAX_SKILL_BYTES or len(entries) >= _MAX_SKILL_FILES:
                        raise ValueError("Skill resources exceed the supported bounds")
                    entries.append(candidate.relative_to(base).as_posix())
            resources[folder] = sorted(entries)
    return (
        "# Skill: "
        + name
        + "\nDescription: "
        + str(metadata["description"])
        + "\nResources: "
        + json.dumps(resources)
        + "\n\n"
        + body
    )

def read_skill_reference(name, rel_path):
    """Stage-3 selective read; only references/ and assets/ are readable."""
    base = _skill_dir(name)
    target = _safe_resource_file(base, rel_path, ("references", "assets"))
    if not target.is_file():
        raise FileNotFoundError("Skill resource is unavailable")
    if target.stat().st_size > _MAX_SKILL_BYTES:
        raise ValueError("Skill resource exceeds the size limit")
    return target.read_text(encoding="utf-8")

def run_skill_script(name, script_rel_path, args=None):
    """Run a bounded script from this Skill's scripts/ directory."""
    base = _skill_dir(name)
    if not isinstance(script_rel_path, str) or "\\" in script_rel_path or "\x00" in script_rel_path:
        raise ValueError("Invalid Skill script path")
    relative = Path(script_rel_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Invalid Skill script path")
    script_path = relative.as_posix()
    if relative.parts[0] != "scripts":
        script_path = "scripts/" + script_path
    target = _safe_resource_file(base, script_path, ("scripts",))
    if target.suffix not in {".py", ".sh"}:
        raise ValueError("Skill scripts must be Python or shell files")
    if not target.is_file() or target.stat().st_size > _MAX_SKILL_BYTES:
        raise FileNotFoundError("Skill script is unavailable")
    command = ["python3", str(target)] if target.suffix == ".py" else ["bash", str(target)]
    if args is not None:
        if not isinstance(args, list) or len(args) > 20:
            raise ValueError("Too many Skill script arguments")
        normalized_args = []
        for arg in args:
            value = str(arg)
            if "\x00" in value or len(value.encode("utf-8")) > 4096:
                raise ValueError("Skill script argument is invalid or too large")
            normalized_args.append(value)
        command.extend(normalized_args)
    result = subprocess.run(command, cwd=str(base), capture_output=True, text=True, timeout=60, check=False)
    output = result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")
    return output[-20000:]

def save_skill(name, description, instructions_md, scripts=None, references=None):
    """Create/update a Skill in this session's isolated Volume."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or len(name) > 64:
        raise ValueError("Invalid skill name")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("Skill description must contain 1-1024 characters")
    if (
        not isinstance(instructions_md, str)
        or not instructions_md.strip()
        or len(instructions_md.encode("utf-8")) > 200000
    ):
        raise ValueError("Skill instructions are too large")
    if (scripts is not None and not isinstance(scripts, dict)) or (
        references is not None and not isinstance(references, dict)
    ):
        raise ValueError("Skill resources must be mappings")
    resources = []
    for folder, file_map in (("scripts", scripts or {}), ("references", references or {})):
        if len(file_map) > 20:
            raise ValueError("Skill resources must be a mapping with at most 20 files")
        seen = set()
        for rel_name, content in file_map.items():
            filename = str(rel_name)
            if (
                not filename
                or Path(filename).name != filename
                or filename in {".", ".."}
                or "\\" in filename
                or "\x00" in filename
                or filename in seen
                or not isinstance(content, str)
            ):
                raise ValueError("Invalid Skill resource")
            seen.add(filename)
            if len(content.encode("utf-8")) > 200000:
                raise ValueError("Skill resource is too large")
            if folder == "scripts" and Path(filename).suffix not in {".py", ".sh"}:
                raise ValueError("Skill scripts must be Python or shell files")
            resources.append((folder, filename, content))
    total_resource_bytes = sum(len(content.encode("utf-8")) for _, _, content in resources)
    if len(instructions_md.encode("utf-8")) + total_resource_bytes > 2_000_000:
        raise ValueError("Skill exceeds the total resource size limit")

    base = _skill_dir(name)
    base.mkdir(parents=True, exist_ok=True)
    frontmatter = "---\nname: " + name + "\ndescription: " + json.dumps(description.strip()) + "\n---\n\n"
    (base / "SKILL.md").write_text(frontmatter + instructions_md.strip() + "\n", encoding="utf-8")
    for folder, filename, content in resources:
        directory = base / folder
        if directory.is_symlink():
            raise ValueError("Skill resource directories may not be symbolic links")
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / filename
        if destination.is_symlink():
            raise ValueError("Skill resource cannot replace a symbolic link")
        destination.write_text(content, encoding="utf-8")
    return "Saved Skill " + name
'''


def parse_and_validate_skill_md(folder_name: str, raw_text: str) -> tuple[dict[str, str], str]:
    """Validate Agent Skills frontmatter and return string metadata plus body."""
    if not isinstance(folder_name, str) or not SKILL_NAME_RE.fullmatch(folder_name) or len(folder_name) > 64:
        raise ValueError("Skill folder name must be lowercase alphanumeric words separated by single hyphens")
    match = FRONTMATTER_RE.match(raw_text)
    if match is None:
        raise ValueError("SKILL.md must start at byte 0 with YAML frontmatter delimited by '---'")
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError("SKILL.md frontmatter is not valid YAML") from exc
    if not isinstance(parsed, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in parsed.items()):
        raise ValueError("Skill frontmatter fields must be string keys and values")
    metadata = dict(parsed)
    if metadata.get("name") != folder_name:
        raise ValueError(f"SKILL.md frontmatter name {metadata.get('name')!r} must match folder name {folder_name!r}")
    description = metadata.get("description", "")
    if not description.strip() or len(description) > 1024:
        raise ValueError("Skill description must contain 1-1024 characters")
    if len(match.group(2).strip()) == 0:
        raise ValueError("Skill instructions must not be empty")
    return metadata, match.group(2).strip()


def _validated_relative_file_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Skill resource path must be relative and remain inside the Skill")
    return path


@dataclass(slots=True)
class SkillManager:
    """Synchronize trusted local Skills with one session's private Volume path."""

    sandbox_mgr: DaytonaSandboxManager
    local_skills_dir: Path = Path(".fleet/skills")
    bundled_skills_dir: Path | None = BUNDLED_SKILLS_DIR

    def _skill_dirs(self) -> dict[str, Path]:
        """Bundled Skills, then local ones; a local Skill replaces a bundled Skill of the same name."""
        selected: dict[str, Path] = {}
        for root in (self.bundled_skills_dir, self.local_skills_dir):
            if root is None:
                continue
            if root.is_symlink():
                raise ValueError("A Skills directory may not be a symbolic link")
            if not root.is_dir():
                continue
            for skill_md in sorted(root.glob("*/SKILL.md")):
                name = skill_md.parent.name
                if name in selected:
                    _LOGGER.info("Local Skill %s replaces the bundled Skill of the same name", name)
                selected[name] = skill_md.parent
        return selected

    def sync_local_skills_to_volume(self) -> int:
        """Upload validated bundled and local Skills; developer-only `.agents/skills` is never read."""
        count = 0
        total_bytes = 0
        staged_files: list[tuple[str, bytes]] = []
        bundled_paths: set[str] = set()
        for skill_dir in self._skill_dirs().values():
            bundled = self.bundled_skills_dir is not None and skill_dir.parent == self.bundled_skills_dir
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_symlink() or skill_md.is_symlink() or skill_md.stat().st_size > MAX_SKILL_FILE_BYTES:
                raise ValueError("Local Skills and their instructions may not be symbolic links or oversized")
            parse_and_validate_skill_md(skill_dir.name, skill_md.read_text(encoding="utf-8"))
            file_count = 0
            for file_path in sorted(skill_dir.rglob("*")):
                if file_path.is_symlink():
                    raise ValueError(f"Skill resources may not be symbolic links: {file_path}")
                if not file_path.is_file():
                    continue
                relative = file_path.relative_to(skill_dir.parent).as_posix()
                _validated_relative_file_path(relative)
                content = file_path.read_bytes()
                if len(content) > MAX_SKILL_FILE_BYTES:
                    raise ValueError(f"Skill resource exceeds {MAX_SKILL_FILE_BYTES} bytes: {relative}")
                total_bytes += len(content)
                file_count += 1
                if file_count > MAX_SKILL_FILES or total_bytes > MAX_SKILL_TOTAL_BYTES:
                    raise ValueError("Local Skills exceed the synchronization bounds")
                staged_files.append((f"skills/{relative}", content))
                if bundled:
                    bundled_paths.add(f"skills/{relative}")
            count += 1
            if count > MAX_SKILL_FILES:
                raise ValueError("Too many local Skills to synchronize")

        remote_manifest = json.dumps([relative for relative, _ in staged_files])
        inspection_code = (
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
        with mlflow.start_span(name="skills.inspect_remote", span_type=SpanType.TOOL) as span:
            span.set_inputs({"file_count": len(staged_files)})
            try:
                existing = json.loads(self.sandbox_mgr.exec_remote_python(inspection_code) or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Could not inspect existing session Skills before synchronization") from exc
            span.set_outputs({"existing": len(existing) if isinstance(existing, dict) else None})
        if not isinstance(existing, dict):
            raise ValueError("Existing session Skill manifest is invalid")
        changed = {
            relative
            for relative, content in staged_files
            if relative in existing and existing[relative] != hashlib.sha256(content).hexdigest()
        }
        # Bundled Skills belong to this Fleet version, so a newer bundled file replaces the session copy.
        conflicts = sorted(changed - bundled_paths)
        if conflicts:
            raise ValueError("Skill sync conflicts with existing session files: " + ", ".join(conflicts))
        for relative in sorted(changed):
            _LOGGER.info("Updating bundled Skill file %s in the session workspace", relative)
        # One validated batch upload instead of a check and an upload per file.
        uploads = {
            relative: content for relative, content in staged_files if relative not in existing or relative in changed
        }
        with mlflow.start_span(name="skills.upload", span_type=SpanType.TOOL) as span:
            byte_count = sum(map(len, uploads.values()))
            span.set_inputs({"file_count": len(uploads), "byte_count": byte_count})
            self.sandbox_mgr.write_workspace_files(uploads)
            span.set_outputs({"uploaded": len(uploads)})
        return count

    def export_volume_skills_to_local(self) -> list[str]:
        """Pull runtime-authored Skills into the separate local Fleet Skills folder."""
        code = (
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
        files = json.loads(self.sandbox_mgr.exec_remote_python(code) or "[]")
        if not isinstance(files, list):
            raise ValueError("Exported Skill manifest is invalid")
        validated: list[tuple[PurePosixPath, bytes]] = []
        skills: dict[str, str] = {}
        total_bytes = 0
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("rel"), str)
                or not isinstance(item.get("b64"), str)
            ):
                raise ValueError("Exported Skill manifest entry is invalid")
            relative = _validated_relative_file_path(item["rel"])
            if "\\" in item["rel"] or len(relative.parts) < 2 or not SKILL_NAME_RE.fullmatch(relative.parts[0]):
                raise ValueError("Exported Skill path is invalid")
            if any(existing == relative for existing, _ in validated):
                raise ValueError("Exported Skill manifest contains duplicate paths")
            content = base64.b64decode(item["b64"], validate=True)
            if len(content) > MAX_SKILL_FILE_BYTES:
                raise ValueError("Exported Skill file exceeds the size limit")
            total_bytes += len(content)
            if total_bytes > MAX_SKILL_TOTAL_BYTES or len(validated) >= MAX_SKILL_FILES:
                raise ValueError("Exported Skills exceed the synchronization bounds")
            if relative.parts[-1] == "SKILL.md":
                skills[relative.parts[0]] = content.decode("utf-8")
            validated.append((relative, content))
        for name, raw_text in skills.items():
            parse_and_validate_skill_md(name, raw_text)
        names = {relative.parts[0] for relative, _ in validated}
        if names != set(skills):
            raise ValueError("Every exported Skill must include its validated SKILL.md")

        exported: set[str] = set()
        if self.local_skills_dir.is_symlink():
            raise ValueError("The local Skills directory may not be a symbolic link")
        root = self.local_skills_dir.resolve()
        destinations: list[tuple[str, Path, bytes]] = []
        for relative, content in validated:
            lexical_destination = root / Path(*relative.parts)
            if lexical_destination.is_symlink() or any(
                parent.is_symlink() for parent in lexical_destination.parents if parent != root
            ):
                raise ValueError("Exported Skill path may not traverse a symbolic link")
            destination = lexical_destination.resolve()
            if destination != root and root not in destination.parents:
                raise ValueError("Exported Skill path escapes the local Skills directory")
            if destination.exists() and (not destination.is_file() or destination.read_bytes() != content):
                raise ValueError(f"Skill export conflicts with existing local file: {relative.as_posix()}")
            destinations.append((relative.parts[0], destination, content))

        for name, destination, content in destinations:
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            exported.add(name)
        exported.update(name for name, _, _ in destinations)
        return sorted(exported)

    def build_catalog_xml(self) -> str:
        """Return the compact Stage-1 catalog from the current sandbox."""
        skills = json.loads(
            self.sandbox_mgr.exec_remote_python(SANDBOX_SKILL_HELPERS_CODE + "\nprint(json.dumps(list_skills()))")
            or "[]"
        )
        return catalog_xml(skills)


def catalog_xml(skills: list[dict[str, Any]]) -> str:
    """Render a stable and XML-escaped Stage-1 catalog."""
    if not skills:
        return "<available_skills>No Skills installed.</available_skills>"
    lines = ["<available_skills>"]
    for skill in skills:
        name = html.escape(str(skill.get("name", "")), quote=True)
        description = html.escape(str(skill.get("description", "")), quote=False)
        lines.append(f'  <skill name="{name}">{description}</skill>')
    lines.append("</available_skills>")
    return "\n".join(lines)
