"""Local Sign in with ChatGPT registrations and subscription-backed inference."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from fcntl import LOCK_EX, flock
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import dspy
import httpx
import jwt
from dspy.lm15 import Message, Request, Response, Usage
from jwt import PyJWKClient

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
CALLBACK_PATH = "/auth/chatgpt/callback"
CALLBACK_URI = f"http://127.0.0.1:8000{CALLBACK_PATH}"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = frozenset({"resource.invoke", "chatgpt.tokens.use.direct"})
PREFIX = "chatgpt/"
CONNECT = "Reconnect your account in Settings → Providers."
USAGE = "Check your allowance in ChatGPT Settings → Usage."
_STORE_ERROR = "ChatGPT credential storage is unavailable. Check the owner-only local files."
_DEFAULTS_ERROR = "ChatGPT model defaults are unavailable. Check the local model settings file."
_TERMINAL_REFRESH_ERRORS = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)


class PlanError(RuntimeError):
    """A fixed, user-visible message with no upstream response or credentials."""


def is_plan_model(value: str | None) -> bool:
    return bool(value and value.startswith(PREFIX))


def _model_reference(reference: str) -> tuple[str, str]:
    profile_id, sep, model = reference.removeprefix(PREFIX).partition("/")
    if not is_plan_model(reference) or not sep or not profile_id or not model:
        raise PlanError("Select a ChatGPT model from the model picker.")
    return profile_id, model


@contextmanager
def _locked_json(path: Path, error_message: str) -> Generator[dict | None]:
    try:
        directory = path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise PlanError(error_message)
        lock = path.with_suffix(".lock")
        with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as fd:
            flock(fd, LOCK_EX)
            if path.is_symlink():
                raise PlanError(error_message)
            data = None
            if path.exists():
                if not path.is_file() or path.stat().st_mode & 0o077:
                    raise PlanError(error_message)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    raise PlanError(error_message) from None
                if not isinstance(data, dict):
                    raise PlanError(error_message)
            yield data
    except OSError:
        raise PlanError(error_message) from None


def _save_json(path: Path, data: dict) -> None:
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as fd:
        pending = Path(fd.name)
        try:
            json.dump(data, fd)
            fd.flush()
            os.fsync(fd.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


class CallbackLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, version, status = args
            if isinstance(path, str) and path.split("?", 1)[0] == CALLBACK_PATH:
                record.args = (client, method, CALLBACK_PATH, version, status)
        return True


@dataclass(frozen=True, repr=False)
class Authorization:
    verifier: str
    nonce: str
    profile_id: str | None
    client_id: str
    expires: float


@dataclass
class ChatGPTPlan:
    path: Path
    defaults_path: Path
    _attempts: dict[str, Authorization] = field(default_factory=dict, init=False, repr=False)
    _attempt_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @contextmanager
    def _locked_store(self) -> Generator[dict]:
        with _locked_json(self.path, _STORE_ERROR) as data:
            if data is None:
                data = {"profiles": {}}
            if not isinstance(data.get("profiles"), dict):
                raise PlanError(_STORE_ERROR)
            yield data

    def _save(self, data: dict) -> None:
        for profile in data["profiles"].values():
            profile.pop("id_token", None)
        _save_json(self.path, data)

    @contextmanager
    def _locked_defaults(self) -> Generator[dict[str, str]]:
        with _locked_json(self.defaults_path, _DEFAULTS_ERROR) as data:
            needs_import = data is None
            if needs_import:
                try:
                    with self._locked_store() as legacy:
                        data = legacy.get("defaults", {})
                except PlanError:
                    raise PlanError(_DEFAULTS_ERROR) from None
            if not isinstance(data, dict) or any(
                role not in {"primary", "sub"}
                or not isinstance(reference, str)
                or not is_plan_model(reference)
                for role, reference in data.items()
            ):
                raise PlanError(_DEFAULTS_ERROR)
            if needs_import:
                _save_json(self.defaults_path, data)
            yield data

    def defaults(self) -> dict[str, str]:
        with self._locked_defaults() as data:
            return dict(data)

    def status(self) -> dict:
        defaults = self.defaults()
        with self._locked_store() as data:
            active = data.get("active")
            profiles = [
                {
                    "id": profile_id,
                    "label": profile["label"],
                    "status": profile["status"],
                }
                for profile_id, profile in data["profiles"].items()
            ]
            selected = data["profiles"].get(active)
            models = (
                [
                    {
                        "id": f"{PREFIX}{active}/{model['slug']}",
                        "label": f"{model['display_name']} · Using ChatGPT plan",
                    }
                    for model in selected.get("models", [])
                ]
                if selected and selected["status"] == "connected"
                else []
            )
            return {
                "profiles": profiles,
                "active": active,
                "models": models,
                "defaults": defaults,
            }

    def start(self, profile_id: str | None) -> str:
        with self._locked_store() as data:
            profile = data["profiles"].get(profile_id)
            if profile_id and not profile:
                raise PlanError("Select a saved ChatGPT account.")
            if "host_id" not in data:
                data["host_id"] = f"urn:uuid:{uuid4()}"
                self._save(data)
            host_id = data["host_id"]
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        client_id = profile["client_id"] if profile else "dynamic_agent_client"
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        params = {
            "client_id": client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": CALLBACK_URI,
            "scope": SCOPE,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if profile is None:
            params["agent_name_hint"] = "Fleet-RLM"
        elif profile["status"] == "consent_required":
            params["prompt"] = "consent"
        with self._attempt_lock:
            now = time.monotonic()
            self._attempts = {k: v for k, v in self._attempts.items() if v.expires > now}
            if len(self._attempts) >= 16:
                raise PlanError(
                    "Too many sign-in attempts. Wait for an attempt to expire and try again."
                )
            self._attempts[state] = Authorization(verifier, nonce, profile_id, client_id, now + 300)
        return f"{ISSUER}/api/accounts/authorize?{urlencode(params)}"

    def callback(
        self, state: str, code: str | None, client_id: str | None, error: str | None
    ) -> tuple[bool, str | None]:
        with self._attempt_lock:
            attempt = self._attempts.pop(state, None)
        if attempt is None or attempt.expires <= time.monotonic():
            return False, "Sign-in expired. Try again."
        try:
            if error:
                raise PlanError("ChatGPT sign-in was declined. Connect again to grant plan usage.")
            if attempt.profile_id and client_id and client_id != attempt.client_id:
                raise PlanError("ChatGPT returned a different account registration.")
            issued = attempt.client_id if attempt.profile_id else client_id
            if not code or not issued or issued == "dynamic_agent_client":
                raise PlanError("ChatGPT sign-in was incomplete. Try again.")
            return self._complete_sign_in(attempt, issued, code)
        except PlanError as exc:
            return False, str(exc)
        except (KeyError, TypeError, ValueError, jwt.PyJWTError):
            return False, "ChatGPT sign-in could not be verified. Try again."

    def _complete_sign_in(
        self, attempt: Authorization, issued: str, code: str
    ) -> tuple[bool, str | None]:
        with self._locked_store() as data:
            tokens = _token_request(
                grant_type="authorization_code",
                client_id=issued,
                code=code,
                code_verifier=attempt.verifier,
                redirect_uri=CALLBACK_URI,
            )
            claims = _verify_identity(tokens["id_token"], issued, attempt.nonce)
            previous = data["profiles"].get(attempt.profile_id)
            if attempt.profile_id and (
                not previous
                or (previous["issuer"], previous["subject"]) != (claims["iss"], claims["sub"])
            ):
                raise PlanError(
                    "ChatGPT returned a different account. "
                    "Choose the original account or add a new one."
                )
            profile_id = attempt.profile_id or next(
                (
                    key
                    for key, value in data["profiles"].items()
                    if value["client_id"] == issued and value["subject"] == claims["sub"]
                ),
                str(uuid4()),
            )
            previous = data["profiles"].get(profile_id)
            label = (
                previous["label"]
                if previous
                else f"{claims.get('email') or 'ChatGPT'} · {profile_id[:8]}"
            )
            profile: dict[str, Any] = {
                "issuer": claims["iss"],
                "subject": claims["sub"],
                "client_id": issued,
                "label": label,
            }
            if previous and "models" in previous:
                profile["models"] = previous["models"]
            _update_grant(profile, tokens)
            models_available = True
            if profile["status"] == "connected":
                try:
                    profile["models"] = _models(profile["access_token"])
                except PlanError:
                    models_available = False
            data["profiles"][profile_id] = profile
            if profile["status"] == "connected" or data.get("active") is None:
                data["active"] = profile_id
            self._save(data)
        if profile["status"] != "connected":
            return False, "ChatGPT plan usage wasn’t granted. Reconnect and approve plan usage."
        return True, None if models_available else "Refresh models to load your ChatGPT catalog."

    def select(self, profile_id: str) -> None:
        with self._locked_store() as data:
            profile = data["profiles"].get(profile_id)
            if not profile:
                raise PlanError("Select a saved ChatGPT account.")
            if data.get("active") != profile_id and profile["status"] == "connected":
                try:
                    profile["models"] = _models(self._access_locked(data, profile))
                except PlanError:
                    # Keep the saved catalog available when discovery is temporarily down.
                    pass
            data["active"] = profile_id
            self._save(data)

    def refresh_models(self, profile_id: str) -> None:
        with self._locked_store() as data:
            profile = self._active_profile(data, profile_id)
            profile["models"] = _models(self._access_locked(data, profile))
            self._save(data)

    def sign_out(self, profile_id: str) -> bool:
        with self._locked_store() as data:
            profile = data["profiles"].get(profile_id)
            if not profile:
                raise PlanError("Select a saved ChatGPT account.")
            revoked = True
            if token := profile.get("refresh_token"):
                try:
                    endpoint = _metadata()["revocation_endpoint"]
                    url = urlsplit(endpoint)
                    if url.scheme != "https" or url.netloc != "auth.openai.com":
                        raise PlanError(CONNECT)
                    for delay in (0, 0.3, 1):
                        time.sleep(delay)
                        try:
                            result = httpx.post(
                                endpoint,
                                data={
                                    "token": token,
                                    "token_type_hint": "refresh_token",
                                    "client_id": profile["client_id"],
                                },
                                timeout=5,
                            )
                            if result.status_code < 500:
                                break
                        except httpx.RequestError:
                            continue
                    else:
                        raise PlanError(CONNECT)
                    revoked = result.status_code == 200
                except (PlanError, KeyError, ValueError, httpx.HTTPError):
                    revoked = False
            _clear_grant(profile, "signed_out")
            self._save(data)
            return revoked

    def set_defaults(self, changes: dict[str, str | None]) -> None:
        with self._locked_defaults() as data:
            selected = {role: value for role, value in changes.items() if value is not None}
            if selected:
                with self._locked_store() as store:
                    for reference in selected.values():
                        self._check_model(store, reference)
            for role, value in changes.items():
                if value is None:
                    data.pop(role, None)
            data.update(selected)
            _save_json(self.defaults_path, data)

    @staticmethod
    def _active_profile(data: dict, profile_id: str) -> dict:
        if data.get("active") != profile_id:
            raise PlanError(
                "Select this model’s ChatGPT account in Settings → Providers, or change models."
            )
        profile = data["profiles"].get(profile_id)
        if not profile or profile["status"] != "connected":
            raise PlanError(CONNECT)
        return profile

    def _check_model(self, data: dict, reference: str) -> dict:
        profile_id, model = _model_reference(reference)
        profile = self._active_profile(data, profile_id)
        if not any(entry["slug"] == model for entry in profile.get("models", [])):
            raise PlanError(
                "This model is unavailable on your ChatGPT plan. Refresh models or choose another."
            )
        return profile

    def access(self, reference: str) -> str:
        with self._locked_store() as data:
            profile = self._check_model(data, reference)
            return self._access_locked(data, profile)

    def validate_model(self, reference: str) -> None:
        with self._locked_store() as data:
            self._check_model(data, reference)

    def _access_locked(self, data: dict, profile: dict) -> str:
        if profile["expires_at"] > time.time() + 60:
            return profile["access_token"]
        refresh = profile.get("refresh_token")
        if not refresh:
            raise PlanError(CONNECT)
        try:
            tokens = _token_request(
                grant_type="refresh_token",
                client_id=profile["client_id"],
                refresh_token=refresh,
            )
        except GrantExpired:
            _clear_grant(profile, "reconnect")
            self._save(data)
            raise PlanError(CONNECT) from None
        _update_grant(profile, tokens)
        self._save(data)
        if profile["status"] != "connected":
            raise PlanError(CONNECT)
        return profile["access_token"]


class GrantExpired(PlanError):
    pass


def _get_json(url: str, *, token: str | None = None) -> dict:
    # Keep transport and response details out of user-visible errors.
    with suppress(httpx.HTTPError, ValueError):
        headers = {"Authorization": f"Bearer {token}"} if token else None
        response = httpx.get(url, headers=headers, timeout=10)
        if response.is_error and token:
            raise _response_error(response.status_code, _error_code(response))
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    raise PlanError("Could not reach ChatGPT. Try again.")


def _metadata() -> dict:
    return _get_json(f"{ISSUER}/.well-known/openid-configuration")


@lru_cache(maxsize=1)
def _jwks_client(uri: str) -> PyJWKClient:
    location = urlsplit(uri)
    if location.scheme != "https" or location.netloc != "auth.openai.com":
        raise PlanError("ChatGPT identity verification is unavailable.")
    return PyJWKClient(uri, timeout=10)


def _verify_identity(token: str, client_id: str, nonce: str) -> dict:
    key = _jwks_client(_metadata()["jwks_uri"]).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "sub", "nonce"]},
    )
    if not claims["sub"] or claims["nonce"] != nonce or claims.get("azp", client_id) != client_id:
        raise PlanError("ChatGPT identity did not match this sign-in attempt.")
    return claims


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error")
        code = error.get("code") if isinstance(error, dict) else error
        return code if isinstance(code, str) else None
    except (ValueError, AttributeError):
        return None


def _token_request(**fields: str) -> dict:
    with suppress(httpx.HTTPError, ValueError):
        response = httpx.post(
            f"{ISSUER}/api/accounts/oauth/token", data={**fields, "resource": RESOURCE}, timeout=15
        )
        if (
            fields.get("grant_type") == "refresh_token"
            and response.is_client_error
            and _error_code(response) in _TERMINAL_REFRESH_ERRORS
        ):
            raise GrantExpired(CONNECT)
        response.raise_for_status()
        tokens = response.json()
        if isinstance(tokens, dict):
            return tokens
    raise PlanError("Could not renew or complete ChatGPT sign-in. Try again.")


def _clear_grant(profile: dict, status: str) -> None:
    for key in ("access_token", "refresh_token", "id_token", "scopes", "expires_at", "models"):
        profile.pop(key, None)
    profile["status"] = status


def _update_grant(profile: dict, tokens: dict) -> None:
    scopes = tokens.get("scope", profile.get("scopes", ""))
    access_token = tokens.get("access_token")
    ttl = tokens.get("expires_in")
    if (
        not isinstance(scopes, str)
        or not PLAN_SCOPES.issubset(scopes.split())
        or not isinstance(access_token, str)
        or not access_token
        or not isinstance(ttl, int)
        or ttl <= 0
        or str(tokens.get("token_type", "Bearer")).lower() != "bearer"
    ):
        _clear_grant(profile, "consent_required")
        return
    profile.update(
        status="connected", scopes=scopes, access_token=access_token, expires_at=time.time() + ttl
    )
    if isinstance(refresh := tokens.get("refresh_token"), str) and refresh:
        profile["refresh_token"] = refresh


def _models(token: str) -> list[dict]:
    models = _get_json(f"{RESOURCE}/models", token=token).get("models")
    if not isinstance(models, list):
        raise PlanError("ChatGPT model catalog could not be loaded. Refresh models to try again.")
    return [
        {"slug": model["slug"], "display_name": model["display_name"]}
        for model in models
        if isinstance(model, dict)
        and model.get("visibility") == "list"
        and isinstance(model.get("slug"), str)
        and model["slug"]
        and isinstance(model.get("display_name"), str)
        and model["display_name"]
    ]


def _response_error(status: int, code: str | None) -> PlanError:
    if (
        code
        in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}
        or status == 429
    ):
        return PlanError(f"ChatGPT plan usage is unavailable. {USAGE}")
    if status in {401, 403}:
        return PlanError(f"ChatGPT authorization or plan usage is unavailable. {CONNECT}")
    return PlanError("ChatGPT could not complete the model request. Retry or choose another model.")


def _request_body(request: Request, model: str, effort: str | None = None) -> dict:
    if request.tools or request.system is not None and not isinstance(request.system, str):
        raise PlanError("ChatGPT plan chat supports text and Fleet workspace actions.")
    inputs = []
    for message in request.messages:
        if message.role not in {"system", "developer", "user", "assistant"} or any(
            part.type != "text" for part in message.parts
        ):
            raise PlanError("ChatGPT plan chat supports text and Fleet workspace actions.")
        inputs.append(
            {
                "role": "developer" if message.role == "system" else message.role,
                "content": message.text or "",
            }
        )
    body: dict = {"model": model, "input": inputs, "stream": True, "store": False}
    if effort is not None:
        body["reasoning"] = {"effort": effort}
    if request.system:
        body["instructions"] = request.system
    return body


def _read_event(line: str, model: str) -> Response | None:
    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
        return None
    try:
        event = json.loads(line[5:])
        kind = event.get("type")
        data = event.get("response") or {}
        if kind in {"response.failed", "error"}:
            error = data.get("error") or event
            raise _response_error(0, error.get("code"))
        if kind == "response.incomplete":
            raise PlanError(
                "ChatGPT returned an incomplete response. Retry or choose another model."
            )
        if kind != "response.completed":
            return None
        content = "".join(
            part["text"]
            for item in data.get("output", [])
            if item.get("type") == "message" and item.get("role") == "assistant"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        if not content.strip():
            raise PlanError("ChatGPT returned no answer text. Retry or choose another model.")
        usage = data.get("usage") or {}
        return Response(
            id=data.get("id"),
            model=model,
            message=Message.assistant(content),
            finish_reason="stop",
            usage=Usage(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise PlanError("ChatGPT returned an invalid response. Try again.") from None


@dataclass(repr=False)
class _PlanConnection:
    service: ChatGPTPlan
    reference: str
    model: str
    effort: str | None = None


class PlanEngine(_PlanConnection):
    def complete(self, request: Request) -> Response:
        body = _request_body(request, self.model, self.effort)
        token = self.service.access(self.reference)
        with suppress(httpx.HTTPError):
            with httpx.stream(
                "POST",
                f"{RESOURCE}/responses",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120,
            ) as response:
                if response.is_error:
                    response.read()
                    raise _response_error(response.status_code, _error_code(response))
                for line in response.iter_lines():
                    if result := _read_event(line, self.model):
                        return result
        raise PlanError("The ChatGPT response stream was interrupted. Try again.")


class AsyncPlanEngine(_PlanConnection):
    async def complete(self, request: Request) -> Response:
        body = _request_body(request, self.model, self.effort)
        token = await asyncio.to_thread(self.service.access, self.reference)
        with suppress(httpx.HTTPError):
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{RESOURCE}/responses",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    if response.is_error:
                        await response.aread()
                        raise _response_error(response.status_code, _error_code(response))
                    async for line in response.aiter_lines():
                        if result := _read_event(line, self.model):
                            return result
        raise PlanError("The ChatGPT response stream was interrupted. Try again.")


def build_lm(
    service: ChatGPTPlan, reference: str, effort: str | None = None
) -> tuple[dspy.LM, str]:
    _, model = _model_reference(reference)
    return dspy.LM(
        f"chatgpt/{model}",
        engine=PlanEngine(service, reference, model, effort),
        async_engine=AsyncPlanEngine(service, reference, model, effort),
        cache=False,
        num_retries=0,
    ), f"ChatGPT plan · {model}"
