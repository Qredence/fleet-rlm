"""Unit tests for the TanStack Start static entrypoint helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_entrypoint_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "scripts" / "ensure_frontend_entrypoint.py"
    spec = importlib.util.spec_from_file_location("ensure_frontend_entrypoint", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ensure_entrypoint_uses_tanstack_manifest_scripts_and_css(tmp_path: Path) -> None:
    module = _load_entrypoint_module()
    dist_dir = tmp_path / "dist"
    client_dir = dist_dir / "client"
    manifest_dir = dist_dir / "server" / "assets"
    (client_dir / "assets").mkdir(parents=True)
    manifest_dir.mkdir(parents=True)

    (client_dir / "assets" / "index-fresh.js").write_text("console.log('fresh')", encoding="utf-8")
    (client_dir / "assets" / "index-fresh.css").write_text("body{}", encoding="utf-8")
    (manifest_dir / "_tanstack-start-manifest_test.js").write_text(
        "var e=()=>({routes:{__root__:{css:[`/assets/index-fresh.css`],"
        "scripts:[{attrs:{type:`module`,async:!0,src:`/assets/index-fresh.js`}}]}}});"
        "export{e as tsrStartManifest};",
        encoding="utf-8",
    )

    index_path = module.ensure_entrypoint(dist_dir)

    html_text = index_path.read_text(encoding="utf-8")
    assert index_path == client_dir / "index.html"
    assert 'src="/assets/index-fresh.js"' in html_text
    assert 'href="/assets/index-fresh.css"' in html_text


def test_ensure_entrypoint_renders_tanstack_server_entry(tmp_path: Path) -> None:
    module = _load_entrypoint_module()
    dist_dir = tmp_path / "dist"
    client_dir = dist_dir / "client"
    server_dir = dist_dir / "server"
    client_dir.mkdir(parents=True)
    server_dir.mkdir(parents=True)
    (server_dir / "server.js").write_text(
        "export default { fetch: async (request) => new Response("
        "`<!doctype html><html><head><title>Rendered</title></head>"
        "<body>${new URL(request.url).pathname}</body></html>`,"
        "{ headers: { 'content-type': 'text/html' } }) };",
        encoding="utf-8",
    )

    index_path = module.ensure_entrypoint(
        dist_dir,
        render_url="http://127.0.0.1:8000/app/workspace",
    )

    html_text = index_path.read_text(encoding="utf-8")
    assert index_path == client_dir / "index.html"
    assert "<title>Rendered</title>" in html_text
    assert "<body>/app/workspace</body>" in html_text


def test_ensure_entrypoint_does_not_overwrite_existing_start_index(tmp_path: Path) -> None:
    module = _load_entrypoint_module()
    dist_dir = tmp_path / "dist"
    client_dir = dist_dir / "client"
    client_dir.mkdir(parents=True)
    index_path = client_dir / "index.html"
    index_path.write_text("<html>start generated</html>", encoding="utf-8")

    resolved = module.ensure_entrypoint(dist_dir)

    assert resolved == index_path
    assert index_path.read_text(encoding="utf-8") == "<html>start generated</html>"
