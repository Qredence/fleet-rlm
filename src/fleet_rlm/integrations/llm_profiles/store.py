"""Persistence layer for LLM provider profiles and role bindings."""

from __future__ import annotations

import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import and_, text

from fleet_rlm.integrations.config.env_file import resolve_env_path
from fleet_rlm.integrations.database.engine import DatabaseManager
from fleet_rlm.integrations.database.models_llm_profiles import LlmProviderProfile, LlmRoleBinding
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

from .crypto import decrypt_api_key, encrypt_api_key
from .types import (
    PROVIDER_DEFAULT_API_BASES,
    LlmProfileBundle,
    LlmProviderProfileRecord,
    LlmProviderType,
    LlmRoleBindingRecord,
    LlmRoleName,
)

ROLE_NAMES: tuple[LlmRoleName, ...] = ("planner", "delegate", "delegate_small")
MASKED_SECRET_SENTINEL = "[REDACTED]"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_role_bindings() -> list[LlmRoleBindingRecord]:
    return [LlmRoleBindingRecord(role=role, profile_id=None, model_id="") for role in ROLE_NAMES]


def _profile_record_from_row(row: LlmProviderProfile) -> LlmProviderProfileRecord:
    return LlmProviderProfileRecord(
        id=row.id,
        name=row.name,
        provider_type=cast(LlmProviderType, row.provider_type),
        api_base=row.api_base or "",
        api_key_ciphertext=row.api_key_ciphertext or "",
        metadata_json=dict(row.metadata_json or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _binding_record_from_row(row: LlmRoleBinding) -> LlmRoleBindingRecord:
    return LlmRoleBindingRecord(
        role=cast(LlmRoleName, row.role),
        profile_id=row.profile_id,
        model_id=row.model_id or "",
    )


class LlmProfileStore(ABC):
    @abstractmethod
    async def load_bundle(self) -> LlmProfileBundle:
        raise NotImplementedError

    @abstractmethod
    async def list_profiles(self) -> list[LlmProviderProfileRecord]:
        raise NotImplementedError

    @abstractmethod
    async def get_profile(self, profile_id: UUID) -> LlmProviderProfileRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def create_profile(
        self,
        *,
        name: str,
        provider_type: LlmProviderType,
        api_base: str | None,
        api_key: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        raise NotImplementedError

    @abstractmethod
    async def update_profile(
        self,
        profile_id: UUID,
        *,
        name: str | None = None,
        provider_type: LlmProviderType | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        raise NotImplementedError

    @abstractmethod
    async def delete_profile(self, profile_id: UUID) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_role_bindings(self) -> list[LlmRoleBindingRecord]:
        raise NotImplementedError

    @abstractmethod
    async def upsert_role_bindings(
        self, bindings: dict[LlmRoleName, tuple[UUID | None, str]]
    ) -> list[LlmRoleBindingRecord]:
        raise NotImplementedError


class JsonLlmProfileStore(LlmProfileStore):
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            self._path = path
        elif profiles_override := os.getenv("FLEET_LLM_PROFILES_PATH"):
            self._path = Path(profiles_override)
        else:
            env_path = resolve_env_path()
            repo_root = env_path.parent
            self._path = repo_root / ".fleet" / "llm-profiles.json"

    def _read_document(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"profiles": [], "role_bindings": []}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write_document(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")

    def _profile_from_dict(self, payload: dict[str, Any]) -> LlmProviderProfileRecord:
        return LlmProviderProfileRecord(
            id=UUID(str(payload["id"])),
            name=str(payload["name"]),
            provider_type=cast(LlmProviderType, payload["provider_type"]),
            api_base=str(payload.get("api_base") or ""),
            api_key_ciphertext=str(payload.get("api_key_ciphertext") or ""),
            metadata_json=dict(payload.get("metadata_json") or {}),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
        )

    def load_bundle_sync(self) -> LlmProfileBundle:
        document = self._read_document()
        profiles = [self._profile_from_dict(item) for item in document.get("profiles", [])]
        bindings = self._bindings_from_document(document)
        return LlmProfileBundle(profiles=profiles, role_bindings=bindings)

    async def load_bundle(self) -> LlmProfileBundle:
        return self.load_bundle_sync()

    def _bindings_from_document(self, document: dict[str, Any]) -> list[LlmRoleBindingRecord]:
        by_role = {item["role"]: item for item in document.get("role_bindings", [])}
        bindings: list[LlmRoleBindingRecord] = []
        for role in ROLE_NAMES:
            payload = by_role.get(role, {})
            profile_id = payload.get("profile_id")
            bindings.append(
                LlmRoleBindingRecord(
                    role=role,
                    profile_id=UUID(str(profile_id)) if profile_id else None,
                    model_id=str(payload.get("model_id") or ""),
                )
            )
        return bindings

    async def list_profiles(self) -> list[LlmProviderProfileRecord]:
        return (await self.load_bundle()).profiles

    async def get_profile(self, profile_id: UUID) -> LlmProviderProfileRecord | None:
        for profile in await self.list_profiles():
            if profile.id == profile_id:
                return profile
        return None

    async def create_profile(
        self,
        *,
        name: str,
        provider_type: LlmProviderType,
        api_base: str | None,
        api_key: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        document = self._read_document()
        now = _utc_now().isoformat()
        profile = LlmProviderProfileRecord(
            id=uuid.uuid4(),
            name=name.strip(),
            provider_type=provider_type,
            api_base=(api_base or PROVIDER_DEFAULT_API_BASES[provider_type]).strip(),
            api_key_ciphertext=encrypt_api_key(api_key.strip()),
            metadata_json=dict(metadata_json or {}),
            created_at=_parse_dt(now),
            updated_at=_parse_dt(now),
        )
        document.setdefault("profiles", []).append(_profile_to_dict(profile))
        document.setdefault("role_bindings", [])
        self._write_document(document)
        return profile

    async def update_profile(
        self,
        profile_id: UUID,
        *,
        name: str | None = None,
        provider_type: LlmProviderType | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        document = self._read_document()
        profiles = document.setdefault("profiles", [])
        for _index, payload in enumerate(profiles):
            if str(payload.get("id")) != str(profile_id):
                continue
            if name is not None:
                payload["name"] = name.strip()
            if provider_type is not None:
                payload["provider_type"] = provider_type
            if api_base is not None:
                payload["api_base"] = api_base.strip()
            if clear_api_key:
                payload["api_key_ciphertext"] = ""
            elif api_key is not None and api_key.strip():
                payload["api_key_ciphertext"] = encrypt_api_key(api_key.strip())
            if metadata_json is not None:
                payload["metadata_json"] = metadata_json
            payload["updated_at"] = _utc_now().isoformat()
            self._write_document(document)
            return self._profile_from_dict(payload)
        raise KeyError(f"Profile not found: {profile_id}")

    async def delete_profile(self, profile_id: UUID) -> None:
        document = self._read_document()
        document["profiles"] = [item for item in document.get("profiles", []) if str(item.get("id")) != str(profile_id)]
        for binding in document.get("role_bindings", []):
            if str(binding.get("profile_id")) == str(profile_id):
                binding["profile_id"] = None
                binding["model_id"] = ""
        self._write_document(document)

    async def list_role_bindings(self) -> list[LlmRoleBindingRecord]:
        return self._bindings_from_document(self._read_document())

    async def upsert_role_bindings(
        self,
        bindings: dict[LlmRoleName, tuple[UUID | None, str]],
    ) -> list[LlmRoleBindingRecord]:
        document = self._read_document()
        by_role = {item["role"]: item for item in document.setdefault("role_bindings", [])}
        for role in ROLE_NAMES:
            profile_id, model_id = bindings.get(role, (None, ""))
            by_role[role] = {
                "role": role,
                "profile_id": str(profile_id) if profile_id else None,
                "model_id": model_id,
            }
        document["role_bindings"] = [by_role[role] for role in ROLE_NAMES if role in by_role]
        self._write_document(document)
        return self._bindings_from_document(document)


class PostgresLlmProfileStore(LlmProfileStore):
    def __init__(self, db_manager: DatabaseManager, *, identity: IdentityUpsertResult | None = None) -> None:
        self._db_manager = db_manager
        self._identity = identity

    async def _ensure_default_bindings(self, session) -> None:
        identity = self._require_identity()
        for role in ROLE_NAMES:
            stmt = (
                insert(LlmRoleBinding)
                .values(
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    workspace_id=identity.workspace_id,
                    role=role,
                    profile_id=None,
                    model_id="",
                )
                .on_conflict_do_nothing(
                    index_elements=[LlmRoleBinding.tenant_id, LlmRoleBinding.user_id, LlmRoleBinding.role]
                )
            )
            await session.execute(stmt)
        await session.flush()

    def _require_identity(self) -> IdentityUpsertResult:
        if self._identity is None or self._identity.user_id is None:
            raise RuntimeError("Postgres LLM profile access requires a persisted authenticated identity.")
        return self._identity

    async def _set_request_context(self, session) -> IdentityUpsertResult:
        identity = self._require_identity()
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(identity.tenant_id)}
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(identity.user_id)}
        )
        await session.execute(
            text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
            {"workspace_id": "" if identity.workspace_id is None else str(identity.workspace_id)},
        )
        return identity

    def _owner_filter(self, model):
        identity = self._require_identity()
        return and_(model.tenant_id == identity.tenant_id, model.user_id == identity.user_id)

    async def load_bundle(self) -> LlmProfileBundle:
        profiles = await self.list_profiles()
        bindings = await self.list_role_bindings()
        return LlmProfileBundle(profiles=profiles, role_bindings=bindings)

    async def list_profiles(self) -> list[LlmProviderProfileRecord]:
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            rows = (
                (
                    await session.execute(
                        select(LlmProviderProfile)
                        .where(self._owner_filter(LlmProviderProfile))
                        .order_by(LlmProviderProfile.name)
                    )
                )
                .scalars()
                .all()
            )
            return [_profile_record_from_row(row) for row in rows]

    async def get_profile(self, profile_id: UUID) -> LlmProviderProfileRecord | None:
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            row = (
                await session.execute(
                    select(LlmProviderProfile).where(
                        and_(LlmProviderProfile.id == profile_id, self._owner_filter(LlmProviderProfile))
                    )
                )
            ).scalar_one_or_none()
            return _profile_record_from_row(row) if row is not None else None

    async def create_profile(
        self,
        *,
        name: str,
        provider_type: LlmProviderType,
        api_base: str | None,
        api_key: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        identity = self._require_identity()
        row = LlmProviderProfile(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            name=name.strip(),
            provider_type=provider_type,
            api_base=(api_base or PROVIDER_DEFAULT_API_BASES[provider_type]).strip(),
            api_key_ciphertext=encrypt_api_key(api_key.strip()),
            metadata_json=dict(metadata_json or {}),
        )
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            session.add(row)
            await session.flush()
            await self._ensure_default_bindings(session)
            await session.refresh(row)
            return _profile_record_from_row(row)

    async def update_profile(
        self,
        profile_id: UUID,
        *,
        name: str | None = None,
        provider_type: LlmProviderType | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        metadata_json: dict[str, Any] | None = None,
    ) -> LlmProviderProfileRecord:
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            row = (
                await session.execute(
                    select(LlmProviderProfile).where(
                        and_(LlmProviderProfile.id == profile_id, self._owner_filter(LlmProviderProfile))
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Profile not found: {profile_id}")
            if name is not None:
                row.name = name.strip()
            if provider_type is not None:
                row.provider_type = provider_type
            if api_base is not None:
                row.api_base = api_base.strip()
            if clear_api_key:
                row.api_key_ciphertext = ""
            elif api_key is not None and api_key.strip():
                row.api_key_ciphertext = encrypt_api_key(api_key.strip())
            if metadata_json is not None:
                row.metadata_json = metadata_json
            await session.flush()
            await session.refresh(row)
            return _profile_record_from_row(row)

    async def delete_profile(self, profile_id: UUID) -> None:
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            await session.execute(
                delete(LlmProviderProfile).where(
                    and_(LlmProviderProfile.id == profile_id, self._owner_filter(LlmProviderProfile))
                )
            )
            bindings = (
                await session.execute(
                    select(LlmRoleBinding).where(
                        and_(LlmRoleBinding.profile_id == profile_id, self._owner_filter(LlmRoleBinding))
                    )
                )
            ).scalars()
            for binding in bindings:
                binding.profile_id = None
                binding.model_id = ""

    async def list_role_bindings(self) -> list[LlmRoleBindingRecord]:
        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            rows = (
                (
                    await session.execute(
                        select(LlmRoleBinding).where(self._owner_filter(LlmRoleBinding)).order_by(LlmRoleBinding.role)
                    )
                )
                .scalars()
                .all()
            )
            existing_roles = {row.role for row in rows}
            if len(existing_roles) >= len(ROLE_NAMES):
                return [_binding_record_from_row(row) for row in rows]

        async with self._db_manager.session() as session, session.begin():
            await self._set_request_context(session)
            await self._ensure_default_bindings(session)
            rows = (
                (
                    await session.execute(
                        select(LlmRoleBinding).where(self._owner_filter(LlmRoleBinding)).order_by(LlmRoleBinding.role)
                    )
                )
                .scalars()
                .all()
            )
            return [_binding_record_from_row(row) for row in rows]

    async def upsert_role_bindings(
        self,
        bindings: dict[LlmRoleName, tuple[UUID | None, str]],
    ) -> list[LlmRoleBindingRecord]:
        async with self._db_manager.session() as session, session.begin():
            identity = await self._set_request_context(session)
            await self._ensure_default_bindings(session)
            for role, (profile_id, model_id) in bindings.items():
                stmt = (
                    insert(LlmRoleBinding)
                    .values(
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        workspace_id=identity.workspace_id,
                        role=role,
                        profile_id=profile_id,
                        model_id=model_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[LlmRoleBinding.tenant_id, LlmRoleBinding.user_id, LlmRoleBinding.role],
                        set_={"profile_id": profile_id, "model_id": model_id, "workspace_id": identity.workspace_id},
                    )
                )
                await session.execute(stmt)
            await session.flush()
            rows = (
                (
                    await session.execute(
                        select(LlmRoleBinding).where(self._owner_filter(LlmRoleBinding)).order_by(LlmRoleBinding.role)
                    )
                )
                .scalars()
                .all()
            )
            return [_binding_record_from_row(row) for row in rows]


def resolve_profile_store(
    db_manager: DatabaseManager | None,
    *,
    identity: IdentityUpsertResult | None = None,
) -> LlmProfileStore:
    if db_manager is not None and db_manager.database_url:
        return PostgresLlmProfileStore(db_manager, identity=identity)
    return JsonLlmProfileStore()


def decrypt_profile_api_key(profile: LlmProviderProfileRecord) -> str:
    return decrypt_api_key(profile.api_key_ciphertext)


def _profile_to_dict(profile: LlmProviderProfileRecord) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "provider_type": profile.provider_type,
        "api_base": profile.api_base,
        "api_key_ciphertext": profile.api_key_ciphertext,
        "metadata_json": profile.metadata_json,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


__all__ = [
    "JsonLlmProfileStore",
    "LlmProfileStore",
    "MASKED_SECRET_SENTINEL",
    "PostgresLlmProfileStore",
    "ROLE_NAMES",
    "decrypt_profile_api_key",
    "resolve_profile_store",
]
