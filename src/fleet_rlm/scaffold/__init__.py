"""Packaged scaffold assets shipped with fleet-rlm.

This subpackage bundles Claude Code skills that document how to drive
fleet-rlm's recursive DSPy + Daytona runtime. The content is shipped as
package-data; consumers locate it with ``importlib.resources``:

    >>> from importlib.resources import files
    >>> skills_root = files("fleet_rlm.scaffold") / "skills"
    >>> for skill_dir in skills_root.iterdir():
    ...     print(skill_dir.name)

See ``skills/README.md`` for the full consumption model.
"""
