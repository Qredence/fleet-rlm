"""Generate bounded release drafts from reviewed GitHub pull-request metadata."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import tomllib

BRANCH = "automation/release-draft"
START = "<!-- fleet-release:start -->"
END = "<!-- fleet-release:end -->"
CATEGORIES = {"feature": "Features", "fix": "Fixes", "breaking": "Breaking Changes", "maintenance": "Maintenance"}


def api(path: str, data: dict | None = None):
    args = ["gh", "api", path]
    if data is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(data) if data is not None else None, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"GitHub API failed for {path}: exit {result.returncode}")
    return json.loads(result.stdout)


def pages(path: str) -> list:
    result = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, check=True)
    return [item for page in json.loads(result.stdout) for item in page]


def field(body: str, name: str) -> str:
    match = re.search(rf"(?im)^#{{2,3}} {re.escape(name)}\s*\n(.*?)(?=^##+ |\Z)", body, re.S | re.M)
    return match[1].strip() if match else ""


def category(pr: dict, *, historical: bool = False) -> str:
    labels = {label["name"] for label in pr.get("labels", [])}
    selected = labels & {f"release:{kind}" for kind in (*CATEGORIES, "none")}
    if not selected and historical:
        return "other"
    if len(selected) != 1:
        raise SystemExit(f"PR #{pr['number']} requires exactly one release category")
    kind = selected.pop().split(":", 1)[1]
    if kind != "none" and not field(pr.get("body") or "", "Release notes"):
        raise SystemExit(f"PR #{pr['number']} requires a Release notes section")
    if kind == "breaking" and not field(pr.get("body") or "", "Migration"):
        raise SystemExit(f"PR #{pr['number']} requires Migration instructions")
    return kind


def render(prs: list[dict]) -> str:
    groups: dict[str, list[str]] = {}
    for pr in sorted(prs, key=lambda item: item["number"]):
        if pr.get("head", {}).get("ref") == BRANCH:
            continue
        kind = category(pr, historical=True)
        if kind == "none":
            continue
        title = " ".join(pr["title"].split())
        notes = field(pr.get("body") or "", "Release notes") or title
        notes = " ".join(notes.split())
        entry = f"- {notes} ([#{pr['number']}]({pr['html_url']}))"
        if kind == "breaking":
            entry += "\n  Migration: " + " ".join(field(pr["body"], "Migration").split())
        groups.setdefault(kind, []).append(entry)
    return "\n\n".join(
        f"### {heading}\n\n" + "\n".join(groups[kind])
        for kind, heading in {**CATEGORIES, "other": "Other"}.items()
        if kind in groups
    )


def replace_generated(text: str, generated: str) -> str:
    digest = hashlib.sha256(generated.encode()).hexdigest()
    block = f"{START}\n<!-- sha256:{digest} -->\n{generated}\n{END}"
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1:
            raise SystemExit("Ambiguous generated changelog markers")
        begin, finish = text.index(START), text.index(END) + len(END)
        existing = text[begin:finish]
        match = re.fullmatch(
            re.escape(START) + r"\n<!-- sha256:([a-f0-9]{64}) -->\n(.*)\n" + re.escape(END), existing, re.S
        )
        if match is None or hashlib.sha256(match[2].encode()).hexdigest() != match[1]:
            raise SystemExit("Generated changelog was edited; refusing to overwrite")
        if begin < text.index("## [Unreleased]") or re.search(
            r"^## ", text[text.index("## [Unreleased]") + 17 : begin], re.M
        ):
            raise SystemExit("Generated block is outside Unreleased")
        return text[:begin] + block + text[finish:]
    heading = "## [Unreleased]"
    if text.count(heading) != 1:
        raise SystemExit("Expected one Unreleased section")
    return text.replace(heading, heading + "\n\n" + block, 1)


def ancestor(before: str, after: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", before, after], capture_output=True).returncode == 0


def validate(repo: str, number: int) -> None:
    pr = api(f"repos/{repo}/pulls/{number}")
    labels = {item["name"] for item in pr["labels"]}
    if pr["head"]["ref"] != BRANCH or pr["head"]["repo"]["full_name"].lower() != repo.lower():
        category(pr)
        return
    if "release-ready" not in labels:
        raise SystemExit("Release draft requires release-ready before merge")
    main = api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if not ancestor(main, pr["head"]["sha"]):
        raise SystemExit("Release draft is stale; remove release-ready, refresh, and review")


def update(repo: str, sha: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise SystemExit("A full source SHA is required")
    main = api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if main != sha:
        raise SystemExit("Superseded draft update: rerun from current main")
    drafts = [
        pr
        for pr in pages(f"repos/{repo}/pulls?state=open&base=main&per_page=100")
        if pr["head"]["ref"] == BRANCH and pr["head"]["repo"]["full_name"].lower() == repo.lower()
    ]
    if len(drafts) > 1:
        raise SystemExit("Multiple release draft PRs")
    draft = drafts[0] if drafts else None
    if draft and (not draft["draft"] or "release-ready" in {label["name"] for label in draft["labels"]}):
        print("Release draft frozen for human review")
        return
    releases = pages(f"repos/{repo}/releases?per_page=100")
    stable = [
        release
        for release in releases
        if not release["draft"] and not release["prerelease"] and re.fullmatch(r"v\d+\.\d+\.\d+", release["tag_name"])
    ]
    baseline = next(
        (
            release
            for release in sorted(stable, key=lambda item: item["published_at"], reverse=True)
            if ancestor(release["tag_name"], sha)
        ),
        None,
    )
    if baseline is None:
        raise SystemExit("No completed stable release ancestor found")
    project = api(f"repos/{repo}/contents/pyproject.toml?ref={sha}")
    version = tomllib.loads(base64.b64decode(project["content"]).decode())["project"]["version"]
    if baseline["tag_name"] != f"v{version}":
        raise SystemExit(
            "Current version has no completed GitHub release; finish or recover it before preparing another"
        )
    base_sha = subprocess.run(
        ["git", "rev-parse", baseline["tag_name"] + "^{commit}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    prs = [
        pr
        for pr in pages(f"repos/{repo}/pulls?state=closed&base=main&per_page=100")
        if pr.get("merged_at")
        and pr.get("merge_commit_sha")
        and ancestor(pr["merge_commit_sha"], sha)
        and not ancestor(pr["merge_commit_sha"], base_sha)
    ]
    generated = render(prs)
    if not generated:
        print("No user-facing changes to draft")
        return
    source = draft["head"]["sha"] if draft else sha
    content = api(f"repos/{repo}/contents/CHANGELOG.md?ref={source}")
    old = base64.b64decode(content["content"]).decode()
    updated = replace_generated(old, generated)
    if updated == old:
        return
    if draft:
        current_main = base64.b64decode(api(f"repos/{repo}/contents/CHANGELOG.md?ref={sha}")["content"]).decode()
        # Only the generated block may differ; preserve human text and never discard other branch changes.
        if replace_generated(current_main, generated) != updated:
            raise SystemExit("Draft has human changelog edits; review instead of overwriting")
        changed = api(f"repos/{repo}/compare/{sha}...{source}")
        if any(item["filename"] != "CHANGELOG.md" for item in changed.get("files", [])):
            raise SystemExit("Draft has preparation edits; freeze it before updating")
    commit = api(f"repos/{repo}/git/commits/{sha}")
    tree = api(
        f"repos/{repo}/git/trees",
        {
            "base_tree": commit["tree"]["sha"],
            "tree": [{"path": "CHANGELOG.md", "mode": "100644", "type": "blob", "content": updated}],
        },
    )
    new = api(
        f"repos/{repo}/git/commits",
        {"message": "docs: update rolling release draft", "tree": tree["sha"], "parents": [sha]},
    )
    if api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"] != sha:
        raise SystemExit("Main advanced during draft generation; retry from latest main")
    if draft:
        if api(f"repos/{repo}/git/ref/heads/{BRANCH}")["object"]["sha"] != source:
            raise SystemExit("Draft changed during generation; refusing to overwrite")
        # PATCH through gh explicitly; API helper defaults mutations to POST.
        subprocess.run(
            ["gh", "api", "--method", "PATCH", f"repos/{repo}/git/refs/heads/{BRANCH}", "--input", "-"],
            input=json.dumps({"sha": new["sha"], "force": True}),
            text=True,
            check=True,
            capture_output=True,
        )
    else:
        api(f"repos/{repo}/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": new["sha"]})
        api(
            f"repos/{repo}/pulls",
            {
                "title": "Prepare next release",
                "head": BRANCH,
                "base": "main",
                "draft": True,
                "body": (
                    "Review generated notes, prepare an explicit stable version, then apply release-ready. "
                    "Remove that label to refresh."
                ),
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "update"])
    parser.add_argument("--repo", default="Qredence/fleet-rlm")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--sha")
    args = parser.parse_args()
    if not re.fullmatch(r"[\w-]+/[\w.-]+", args.repo):
        parser.error("Invalid repository")
    if args.command == "validate":
        if not args.pr:
            parser.error("validate requires --pr")
        validate(args.repo, args.pr)
    else:
        if not args.sha:
            parser.error("update requires --sha")
        update(args.repo, args.sha)


if __name__ == "__main__":
    main()
