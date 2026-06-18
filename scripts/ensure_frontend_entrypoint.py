#!/usr/bin/env python3
"""Ensure a static TanStack Start client entrypoint exists for FastAPI serving.

TanStack Start emits ``dist/client`` and ``dist/server`` with prerendering disabled,
but it may not emit ``dist/client/index.html``. ``fleet web`` serves the static
client directory from FastAPI, so this helper creates the minimal client entrypoint
only when Start did not already write one.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from pathlib import Path

CLIENT_ENTRY_RE = re.compile(r"clientEntry:`(?P<path>[^`]+)`")
CSS_HREF_RE = re.compile(r"href:`(?P<path>/assets/[^`]+\.css)`")


def _find_manifest(dist_dir: Path) -> Path | None:
    candidates = sorted((dist_dir / "server" / "assets").glob("_tanstack-start-manifest*.js"))
    return candidates[-1] if candidates else None


def _render_start_entrypoint(dist_dir: Path, render_url: str) -> str | None:
    server_entry = dist_dir / "server" / "server.js"
    if not server_entry.is_file():
        return None

    node_script = """
import { pathToFileURL } from 'node:url';

const serverPath = process.env.FLEET_TSS_SERVER_ENTRY;
const renderUrl = process.env.FLEET_TSS_RENDER_URL;
if (!serverPath || !renderUrl) {
  throw new Error('Missing TanStack Start render environment');
}

const server = await import(pathToFileURL(serverPath).href);
const handler = server.default;
if (!handler || typeof handler.fetch !== 'function') {
  throw new Error(`${serverPath} does not export a fetch handler`);
}

const response = await handler.fetch(
  new Request(renderUrl, {
    headers: {
      accept: 'text/html',
      'X-TSS_SHELL': 'true',
    },
  }),
);
if (!response.ok) {
  throw new Error(`TanStack Start render failed with HTTP ${response.status}`);
}

process.stdout.write(await response.text());
process.exit(0);
"""
    env = {
        **os.environ,
        "FLEET_TSS_SERVER_ENTRY": str(server_entry.resolve()),
        "FLEET_TSS_RENDER_URL": render_url,
    }
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    html_text = completed.stdout.strip()
    if not html_text:
        raise ValueError(f"TanStack Start rendered an empty entrypoint from {server_entry}")
    return html_text


def _extract_assets(manifest_path: Path) -> tuple[str, list[str]]:
    content = manifest_path.read_text(encoding="utf-8")
    client_match = CLIENT_ENTRY_RE.search(content)
    if client_match is not None:
        client_entry = client_match.group("path")
    else:
        fallback_match = re.search(r"src:`(?P<path>/assets/index-[^`]+\.js)`", content)
        if fallback_match is not None:
            client_entry = fallback_match.group("path")
        else:
            raise ValueError(f"Could not find clientEntry or fallback index src in {manifest_path}")

    css_paths = sorted(dict.fromkeys(re.findall(r"`(?P<path>/assets/[^`]+\.css)`", content)))
    if not css_paths:
        css_paths = sorted(dict.fromkeys(CSS_HREF_RE.findall(content)))

    return client_entry, css_paths


def ensure_entrypoint(dist_dir: Path, *, render_url: str = "http://127.0.0.1:8000/app/workspace") -> Path:
    """Ensure the client dist root contains an index.html file.

    Existing Start-generated entrypoints are returned as-is and never overwritten.
    """
    client_dir = dist_dir / "client"
    if not client_dir.is_dir():
        raise FileNotFoundError(f"Missing frontend client dist at {client_dir}")

    index_path = client_dir / "index.html"
    if index_path.is_file():
        return index_path

    rendered_html = _render_start_entrypoint(dist_dir, render_url)
    if rendered_html is not None:
        index_path.write_text(rendered_html, encoding="utf-8")
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
    parser.add_argument(
        "--render-url",
        default="http://127.0.0.1:8000/app/workspace",
        help="URL used when rendering the TanStack Start static shell.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        index_path = ensure_entrypoint(args.dist_dir, render_url=args.render_url)
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: frontend entrypoint available at {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
