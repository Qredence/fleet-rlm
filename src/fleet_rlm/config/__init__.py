"""Fleet runtime configuration.

``config.settings`` owns the authoritative ``Settings`` schema and the
schema-derived policy inventory, ``config.loader`` owns TOML policy loading
and environment resolution, and ``config.policy`` owns the loopback-only
editable policy service.  The package root is import-light by design: callers
import the concrete submodule they need.
"""
