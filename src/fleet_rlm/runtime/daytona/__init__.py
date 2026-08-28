"""Daytona-bound runtime assembly.

The only ``runtime/`` corner permitted to import ``fleet_rlm.daytona``:
``run_environment`` owns the per-Turn environment/capability adapters and
provider resource lifecycle, and ``workspace_gateway`` owns the Daytona-backed
Workspace Volume gateway assembly.  Provider-neutral runtime modules must
never import this package.
"""
