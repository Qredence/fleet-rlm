"""Provider-neutral runtime domain models and ports.

Import concrete types from their owning modules. Keeping package initialization
side-effect free avoids loading binding and owned-effect graphs eagerly.
"""

from __future__ import annotations
