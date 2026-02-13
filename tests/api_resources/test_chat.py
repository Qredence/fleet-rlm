# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from tests.utils import assert_matches_type
from fleet_rlm_typescript_sdk import FleetRlmTypescriptSDK, AsyncFleetRlmTypescriptSDK

base_url = os.environ.get("TEST_API_BASE_URL", "http://127.0.0.1:4010")


class TestChat:
    parametrize = pytest.mark.parametrize("client", [False, True], indirect=True, ids=["loose", "strict"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_send_message(self, client: FleetRlmTypescriptSDK) -> None:
        chat = client.chat.send_message(
            message="message",
        )
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_send_message_with_all_params(self, client: FleetRlmTypescriptSDK) -> None:
        chat = client.chat.send_message(
            message="message",
            docs_path="docs_path",
            trace=True,
        )
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_send_message(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.chat.with_raw_response.send_message(
            message="message",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        chat = response.parse()
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_send_message(self, client: FleetRlmTypescriptSDK) -> None:
        with client.chat.with_streaming_response.send_message(
            message="message",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            chat = response.parse()
            assert_matches_type(object, chat, path=["response"])

        assert cast(Any, response.is_closed) is True


class TestAsyncChat:
    parametrize = pytest.mark.parametrize(
        "async_client", [False, True, {"http_client": "aiohttp"}], indirect=True, ids=["loose", "strict", "aiohttp"]
    )

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_send_message(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        chat = await async_client.chat.send_message(
            message="message",
        )
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_send_message_with_all_params(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        chat = await async_client.chat.send_message(
            message="message",
            docs_path="docs_path",
            trace=True,
        )
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_send_message(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.chat.with_raw_response.send_message(
            message="message",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        chat = await response.parse()
        assert_matches_type(object, chat, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_send_message(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.chat.with_streaming_response.send_message(
            message="message",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            chat = await response.parse()
            assert_matches_type(object, chat, path=["response"])

        assert cast(Any, response.is_closed) is True
