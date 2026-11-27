"""Subscription sign-in and DSPy transport tests without a ChatGPT account."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import dspy
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from dspy.lm15 import Config, Message, Request
from dspy.utils.exceptions import LMUnexpectedError
from starlette.requests import Request as HttpRequest

from fleet_rlm import api
from fleet_rlm import chatgpt_plan as plan

MODEL = "gpt-6-luna"
CATALOG = [{"slug": MODEL, "display_name": "GPT-6 Luna"}]


@pytest.fixture
def service(tmp_path):
    return plan.ChatGPTPlan(
        tmp_path / "private" / "credentials.json", tmp_path / "models" / "defaults.json"
    )


def grant(label="first", *, expires_in=3600, scope=plan.SCOPE):
    return {
        "id_token": "identity-token",
        "access_token": f"access-{label}",
        "refresh_token": f"refresh-{label}",
        "expires_in": expires_in,
        "scope": scope,
    }


def state_from_url(url):
    return parse_qs(urlsplit(url).query)["state"][0]


def connect(service, monkeypatch, label="first"):
    monkeypatch.setattr(plan, "_token_request", lambda **fields: grant(label))
    monkeypatch.setattr(
        plan,
        "_verify_identity",
        lambda *_: {"iss": plan.ISSUER, "sub": label, "email": "a@example.com"},
    )
    monkeypatch.setattr(plan, "_models", lambda _: CATALOG)
    state = state_from_url(service.start(None))
    assert service.callback(state, "code", f"oaiapp_{label}", None) == (True, None)
    return service.status()["active"]


def test_registration_replay_and_declined_consent(service, monkeypatch):
    fields = parse_qs(urlsplit(service.start(None)).query)
    assert fields["client_id"] == ["dynamic_agent_client"]
    assert fields["redirect_uri"] == [plan.CALLBACK_URI]
    assert fields["code_challenge_method"] == ["S256"]
    assert set(fields["scope"][0].split()) >= plan.PLAN_SCOPES
    state = fields["state"][0]
    assert service.callback("wrong-state", "code", "oaiapp_test", None)[0] is False
    success, message = service.callback(state, None, None, "access_denied")
    assert not success and "declined" in message
    assert service.callback(state, "code", "oaiapp_test", None) == (
        False,
        "Sign-in expired. Try again.",
    )

    profile = connect(service, monkeypatch)
    status = service.status()
    assert status["models"][0]["id"] == f"chatgpt/{profile}/{MODEL}"
    assert "access-first" not in json.dumps(status)
    assert service.path.stat().st_mode & 0o777 == 0o600
    assert "identity-token" not in service.path.read_text()
    resumed = plan.ChatGPTPlan(service.path, service.defaults_path)
    returning = parse_qs(urlsplit(resumed.start(profile)).query)
    assert returning["ext_agent_host_id"] == fields["ext_agent_host_id"]
    assert returning["client_id"] == ["oaiapp_first"]
    assert "agent_name_hint" not in returning
    assert "id_token_hint" not in returning
    assert "prompt" not in returning


def test_reconnect_cannot_replace_another_identity(service, monkeypatch):
    profile = connect(service, monkeypatch)
    state = state_from_url(service.start(profile))
    monkeypatch.setattr(plan, "_verify_identity", lambda *_: {"iss": plan.ISSUER, "sub": "other"})
    assert service.callback(state, "code", None, None)[0] is False
    assert service.access(f"chatgpt/{profile}/{MODEL}") == "access-first"


def test_reconnect_keeps_saved_models_if_catalog_refresh_fails(service, monkeypatch):
    profile = connect(service, monkeypatch)
    reference = f"chatgpt/{profile}/{MODEL}"
    defaults = {"primary": reference, "sub": reference}
    service.set_defaults(defaults)
    monkeypatch.setattr(plan, "_token_request", lambda **_: grant("renewed"))

    def unavailable(_):
        raise plan.PlanError("Catalog unavailable")

    monkeypatch.setattr(plan, "_models", unavailable)
    state = state_from_url(service.start(profile))
    success, notice = service.callback(state, "code", None, None)
    assert success and "Refresh models" in notice
    assert service.defaults() == defaults
    assert service.status()["models"][0]["id"] == reference
    assert service.access(reference) == "access-renewed"


def test_reconnect_requests_consent_after_a_missing_usage_grant(service, monkeypatch):
    monkeypatch.setattr(plan, "_token_request", lambda **_: grant(scope="openid profile email"))
    monkeypatch.setattr(plan, "_verify_identity", lambda *_: {"iss": plan.ISSUER, "sub": "local"})
    state = state_from_url(service.start(None))
    assert service.callback(state, "code", "oaiapp_local", None)[0] is False
    status = service.status()
    assert status["profiles"][0]["status"] == "consent_required"
    assert status["models"] == []
    assert "access-first" not in service.path.read_text()
    retry = service.start(status["active"])
    fields = parse_qs(urlsplit(retry).query)
    assert fields["prompt"] == ["consent"]
    assert fields["client_id"] == ["oaiapp_local"]
    assert fields["scope"] == [plan.SCOPE]
    monkeypatch.setattr(plan, "_token_request", lambda **_: grant())
    monkeypatch.setattr(plan, "_models", lambda _: CATALOG)
    assert service.callback(state_from_url(retry), "code", None, None) == (True, None)
    assert service.status()["active"] == status["active"]


def test_imports_legacy_defaults_and_prunes_retained_id_tokens(service, monkeypatch):
    profile_id = connect(service, monkeypatch)
    reference = f"chatgpt/{profile_id}/{MODEL}"
    service.defaults_path.unlink()
    legacy = json.loads(service.path.read_text())
    legacy["profiles"][profile_id]["id_token"] = "legacy-identity-token"
    legacy["defaults"] = {"primary": reference, "sub": reference}
    service.path.write_text(json.dumps(legacy))

    assert service.defaults() == legacy["defaults"]
    assert service.defaults_path.stat().st_mode & 0o777 == 0o600
    assert "legacy-identity-token" not in service.start(profile_id)
    service.select(profile_id)
    assert "legacy-identity-token" not in service.path.read_text()
    assert service.access(reference) == "access-first"
    service.set_defaults({"primary": None})
    resumed = plan.ChatGPTPlan(service.path, service.defaults_path)
    assert resumed.defaults() == {"sub": reference}


@pytest.mark.asyncio
async def test_api_key_primary_can_run_when_plan_credentials_are_unreadable(service, monkeypatch):
    profile_id = connect(service, monkeypatch)
    reference = f"chatgpt/{profile_id}/{MODEL}"
    service.set_defaults({"primary": reference, "sub": reference})
    service.path.parent.chmod(0o755)
    request = HttpRequest(
        {
            "type": "http",
            "scheme": "http",
            "path": "/api/sessions/example/chat",
            "client": ("127.0.0.1", 10000),
            "headers": [(b"host", b"127.0.0.1:8000")],
        }
    )
    settings = SimpleNamespace(
        llm_model="openai/primary", sub_llm_model="openai/sub", llm_base_url=None
    )
    runtime = SimpleNamespace(chatgpt=service, settings=settings, sub_lm=dspy.LM("openai/sub"))

    (root_model, _), sub_lm = await api._interactive_models(
        request, runtime, settings, "openai/api-key-model"
    )
    assert root_model == "openai/api-key-model"
    assert sub_lm.model == f"chatgpt/{MODEL}"
    with pytest.raises(LMUnexpectedError) as failure:
        await sub_lm.acall(Request(sub_lm.model, (Message.user("Delegate"),)))
    assert "credential storage is unavailable" in api._turn_failure_message(failure.value, None)

    assert service.defaults() == {"primary": reference, "sub": reference}
    service.set_defaults({"sub": None})
    _, sub_lm = await api._interactive_models(request, runtime, settings, "openai/api-key-model")
    assert sub_lm is runtime.sub_lm


def test_switch_does_not_rebind_defaults_or_session_model(service, monkeypatch):
    first = connect(service, monkeypatch)
    reference = f"chatgpt/{first}/{MODEL}"
    service.set_defaults({"primary": reference, "sub": reference})
    second = connect(service, monkeypatch, "second")
    assert service.defaults() == {"primary": reference, "sub": reference}
    with pytest.raises(plan.PlanError, match="Select this model’s ChatGPT account"):
        service.access(reference)
    assert service.access(f"chatgpt/{second}/{MODEL}") == "access-second"
    refreshed = [{"slug": MODEL, "display_name": "Refreshed model name"}]
    monkeypatch.setattr(plan, "_models", lambda _: refreshed)
    service.select(first)
    assert service.access(reference) == "access-first"
    assert service.status()["models"][0]["label"].startswith("Refreshed model name")


def test_serializes_refresh_and_signs_out_when_revocation_fails(service, monkeypatch):
    profile = connect(service, monkeypatch)
    reference = f"chatgpt/{profile}/{MODEL}"
    with service._locked_store() as data:
        data["profiles"][profile]["expires_at"] = 0
        service._save(data)
    requests = []

    def renew(**fields):
        requests.append(fields)
        return grant("rotated")

    monkeypatch.setattr(plan, "_token_request", renew)
    with ThreadPoolExecutor(4) as workers:
        access = list(workers.map(service.access, [reference] * 4))
    assert access == ["access-rotated"] * 4
    assert requests == [
        {
            "grant_type": "refresh_token",
            "client_id": "oaiapp_first",
            "refresh_token": "refresh-first",
        }
    ]
    monkeypatch.setattr(
        plan,
        "_metadata",
        lambda: {"revocation_endpoint": f"{plan.ISSUER}/revoke"},
    )
    monkeypatch.setattr(plan.httpx, "post", lambda *args, **kwargs: httpx.Response(503))
    monkeypatch.setattr(plan.time, "sleep", lambda _: None)
    assert not service.sign_out(profile)
    assert "refresh-rotated" not in service.path.read_text()
    assert "identity-token" not in service.path.read_text()
    with pytest.raises(plan.PlanError, match="Reconnect"):
        service.access(reference)


@pytest.mark.parametrize(
    ("status", "error", "terminal"),
    [
        (400, "invalid_grant", True),
        (400, "invalid_refresh_token", True),
        (401, "token_expired", True),
        (400, "refresh_token_expired", True),
        (400, "refresh_token_invalidated", True),
        (400, "refresh_token_reused", True),
        (401, "invalid_client", False),
        (503, "refresh_token_expired", False),
    ],
)
def test_only_terminal_refresh_errors_clear_credentials(
    service, monkeypatch, status, error, terminal
):
    token_request = plan._token_request
    profile_id = connect(service, monkeypatch)
    reference = f"chatgpt/{profile_id}/{MODEL}"
    with service._locked_store() as data:
        data["profiles"][profile_id]["expires_at"] = 0
        service._save(data)
    monkeypatch.setattr(plan, "_token_request", token_request)
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"error": error}))
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(plan.httpx, "post", client.post)
        message = "Reconnect" if terminal else "Try again"
        with pytest.raises(plan.PlanError, match=message):
            service.access(reference)

    profile = json.loads(service.path.read_text())["profiles"][profile_id]
    assert profile["status"] == ("reconnect" if terminal else "connected")
    assert profile["client_id"] == "oaiapp_first"
    assert profile.get("refresh_token") == (None if terminal else "refresh-first")
    assert profile.get("access_token") == (None if terminal else "access-first")


def test_validates_id_token_signature_audience_and_nonce(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    monkeypatch.setattr(plan, "_metadata", lambda: {"jwks_uri": f"{plan.ISSUER}/jwks"})
    monkeypatch.setattr(
        jwt.PyJWKClient, "fetch_data", lambda _: {"keys": [{**public, "kid": "public"}]}
    )
    token = jwt.encode(
        {
            "iss": plan.ISSUER,
            "sub": "subscriber",
            "aud": "oaiapp_fleet",
            "exp": int(plan.time.time()) + 300,
            "iat": int(plan.time.time()),
            "nonce": "nonce",
        },
        key,
        algorithm="RS256",
        headers={"kid": "public"},
    )
    assert plan._verify_identity(token, "oaiapp_fleet", "nonce")["sub"] == "subscriber"
    with pytest.raises(jwt.InvalidAudienceError):
        plan._verify_identity(token, "oaiapp_other", "nonce")
    with pytest.raises(plan.PlanError, match="did not match"):
        plan._verify_identity(token, "oaiapp_fleet", "wrong")


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"type": "response.output_text.delta", "delta": "partial"}, "interrupted"),
        ({"type": "response.incomplete"}, "incomplete"),
        (
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "subscription_sharing_usage_limit_exceeded",
                        "message": "secret",
                    },
                },
            },
            "Settings → Usage",
        ),
    ],
)
def test_inference_requires_terminal_success_and_redacts_errors(
    service, monkeypatch, event, message
):
    profile = connect(service, monkeypatch)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text=f"data: {json.dumps(event)}\n\n"),
    )
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(plan.httpx, "stream", client.stream)
        engine = plan.PlanEngine(service, f"chatgpt/{profile}/{MODEL}", MODEL)
        with pytest.raises(plan.PlanError, match=message) as failure:
            engine.complete(Request(MODEL, (Message.user("hi"),)))
    assert "secret" not in str(failure.value)
    assert api._turn_failure_message(failure.value, None) == str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", [None, "high"])
async def test_dspy_sync_and_async_use_subscription_responses(service, monkeypatch, effort):
    profile = connect(service, monkeypatch)
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer access-first"
        event = {
            "type": "response.completed",
            "response": {
                "id": "resp_test",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "<answer>5</answer>"},
                        ],
                    }
                ],
                "usage": {"total_tokens": 10},
            },
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    transport = httpx.MockTransport(respond)
    reference = f"chatgpt/{profile}/{MODEL}"
    lm, _ = plan.build_lm(service, reference, effort)
    prompt = Request(
        lm.model,
        (Message.user("Which digit?"),),
        system="Answer precisely.",
        config=Config(max_tokens=80, temperature=0.2),
    )
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(plan.httpx, "stream", client.stream)
        monkeypatch.setattr(
            plan.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
        )
        assert (await asyncio.to_thread(lm, prompt)).message.text == "<answer>5</answer>"
        with dspy.context(lm=lm, adapter=dspy.XMLAdapter()):
            answer = await dspy.Predict("question -> answer").acall(question="Which digit?")
        assert answer.answer == "5"
    assert len(sent) == 2
    for body in sent:
        assert body["model"] == MODEL and body["stream"] and not body["store"]
        assert "temperature" not in body and "max_output_tokens" not in body
        assert body.get("reasoning") == ({"effort": effort} if effort else None)
    assert sent[0]["input"][-1] == {"role": "user", "content": "Which digit?"}
    assert sent[0]["instructions"] == "Answer precisely."


@pytest.mark.asyncio
async def test_only_local_fleet_can_manage_profiles_or_expose_callback(service, monkeypatch):
    profile_id = connect(service, monkeypatch)
    legacy = json.loads(service.path.read_text())
    legacy["profiles"][profile_id]["id_token"] = "legacy-identity-token"
    service.path.write_text(json.dumps(legacy))
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            api_bearer_token=None, provider_admin_token=None, cors_origins=["http://localhost:5173"]
        ),
        chatgpt=service,
    )
    monkeypatch.setattr(api.app.state, "runtime", runtime, raising=False)
    transport = httpx.ASGITransport(app=api.app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        denied = await client.post(
            "/api/chatgpt/authorize", json={}, headers={"Origin": "https://evil.test"}
        )
        assert denied.status_code == 403
        assert (
            await client.get("/api/chatgpt", headers={"X-Forwarded-For": "127.0.0.1"})
        ).json() == {
            "enabled": False,
        }
        started = await client.post(
            "/api/chatgpt/authorize",
            json={"profile_id": profile_id},
            headers={"Origin": "http://localhost:5173"},
        )
        assert started.status_code == 200
        assert set(started.json()) == {"url"}
        assert "legacy-identity-token" not in started.text and "id_token_hint" not in started.text
        state = state_from_url(started.json()["url"])
        result = await client.get(
            plan.CALLBACK_PATH, params={"state": state, "error": "access_denied"}
        )
        assert result.status_code == 200 and state not in result.text
        assert "declined" in result.text
        assert result.headers["Referrer-Policy"] == "no-referrer"
        replayed = await client.get(plan.CALLBACK_PATH, params={"state": state, "code": "code"})
        assert "Sign-in expired" in replayed.text
        assert (await client.post(f"/api/chatgpt/profiles/{profile_id}/models")).status_code == 204
    transport = httpx.ASGITransport(app=api.app, client=("192.0.2.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        assert (await client.post("/api/chatgpt/authorize", json={})).status_code == 403


def test_callback_access_log_drops_query_and_optimization_rejects_plan():
    path = f"{plan.CALLBACK_PATH}?code=secret"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        "%s %s %s %s %s",
        ("127.0.0.1", "GET", path, "1.1", 200),
        None,
    )
    assert plan.CallbackLogFilter().filter(record)
    assert "secret" not in record.getMessage()
    with pytest.raises(api.HTTPException, match="interactive chat only"):
        api._optimization_role_selection(
            api.LMRoleSelection(provider_id=uuid4(), model=f"chatgpt/{uuid4()}/{MODEL}"),
            "default",
        )
