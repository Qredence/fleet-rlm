# Memory Topology Notes

These notes describe experimental memory-topology concepts that were previously stored under `src/fleet_rlm/memory-topology/` and are now documented outside the package source tree.

## Notes

- [Boundary Detect](memory-topology/boundery-detect.md)
- [Episodic Generation](memory-topology/episodic-generation.md)
- [Semantic Generation](memory-topology/semantic-generation.md)
- [Theme Generator](memory-topology/theme-generator.md)

## Why This Lives in Docs

These files are design/reference notes, not importable runtime modules. Keeping them under `docs/` reduces package noise and keeps `src/fleet_rlm/` focused on executable Python code.
