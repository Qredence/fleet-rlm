#!/usr/bin/env python3
"""Create a static frontend HTML entrypoint when TanStack Start skips prerendering."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

CLIENT_ENTRY_RE = re.compile(r"clientEntry:`(?P<path>[^`]+)`")
CSS_HREF_RE = re.compile(r"href:`(?P<path>/assets/[^`]+\.css)`")


def _find_manifest(dist_dir: Path) -> Path | None:
    candidates = sorted((dist_dir / "server" / "assets").glob("_tanstack-start-manifest*.js"))
    return candidates[-1] if candidates else None


def _extract_assets(manifest_path: Path) -> tuple[str, list[str]]:
    content = manifest_path.read_text(encoding="utf-8")
    client_match = CLIENT_ENTRY_RE.search(content)
    if client_match is None:
        raise ValueError(f"Could not find clientEntry in {manifest_path}")

    css_paths = sorted(dict.fromkeys(CSS_HREF_RE.findall(content)))
    return client_match.group("path"), css_paths


def ensure_entrypoint(dist_dir: Path) -> Path:
    """Ensure the client dist root contains an index.html file."""
    client_dir = dist_dir / "client"
    if not client_dir.is_dir():
        raise FileNotFoundError(f"Missing frontend client dist at {client_dir}")

    index_path = client_dir / "index.html"
    if index_path.is_file():
        return index_path

    manifest_path = _find_manifest(dist_dir)
    if manifest_path is None:
        raise FileNotFoundError(f"Missing TanStack Start manifest under {dist_dir / 'server' / 'assets'}")

    client_entry, css_paths = _extract_assets(manifest_path)
    missing_assets = [path for path in [client_entry, *css_paths] if not (client_dir / path.lstrip("/")).is_file()]
    if missing_assets:
        missing = ", ".join(missing_assets)
        raise FileNotFoundError(f"Manifest references missing frontend assets: {missing}")

    stylesheet_tags = "\n".join(
        f'    <link rel="stylesheet" href="{html.escape(path, quote=True)}" />' for path in css_paths
    )
    html_text = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark light" />
    <title>Qredence</title>
{stylesheet_tags}
    <script type="module" crossorigin src="{html.escape(client_entry, quote=True)}"></script>
  </head>
  <body>
    <noscript>JavaScript is required to use Fleet-RLM.</noscript>
  </body>
</html>
"""
    index_path.write_text(html_text, encoding="utf-8")
    return index_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("src/frontend/dist"),
        help="Frontend build dist directory. Defaults to src/frontend/dist.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        index_path = ensure_entrypoint(args.dist_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: frontend entrypoint available at {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
