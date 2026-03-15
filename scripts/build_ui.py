import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root_dir = Path(__file__).parent.parent.resolve()
    frontend_dir = root_dir / "src" / "frontend"
    target_ui_dir = root_dir / "src" / "fleet_rlm" / "ui"

    if not frontend_dir.exists():
        print(f"Error: Frontend directory not found at {frontend_dir}", file=sys.stderr)
        return 1

    print("Building frontend UI...")

    # Check if pnpm is available
    if shutil.which("pnpm") is None:
        print(
            "Error: 'pnpm' command not found. Please install pnpm (https://pnpm.io).",
            file=sys.stderr,
        )
        return 1

    # Run pnpm install
    print("Running 'pnpm install --frozen-lockfile'...")
    try:
        subprocess.run(
            ["pnpm", "install", "--frozen-lockfile"], cwd=frontend_dir, check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Error running 'pnpm install': {e}", file=sys.stderr)
        return 1

    # Run pnpm run build
    print("Running 'pnpm run build'...")
    try:
        subprocess.run(["pnpm", "run", "build"], cwd=frontend_dir, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running 'pnpm run build': {e}", file=sys.stderr)
        return 1

    source_dist = frontend_dir / "dist"
    if not source_dist.exists():
        print(f"Error: Build failed, {source_dist} not found.", file=sys.stderr)
        return 1

    # Create the target directory and an __init__.py if it doesn't exist
    target_ui_dir.mkdir(parents=True, exist_ok=True)
    init_py = target_ui_dir / "__init__.py"
    if not init_py.exists():
        init_py.touch()

    target_dist = target_ui_dir / "dist"

    # Remove existing target dist
    if target_dist.exists():
        shutil.rmtree(target_dist)

    # Copy new dist
    print(f"Copying build output to {target_dist}...")
    shutil.copytree(source_dist, target_dist)

    print("Frontend build complete and copied successfully!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
