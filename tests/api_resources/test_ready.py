# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from tests.utils import assert_matches_type
from fleet_rlm_typescript_sdk import FleetRlmTypescriptSDK, AsyncFleetRlmTypescriptSDK
from fleet_rlm_typescript_sdk.types import ReadyCheckResponse

base_url = os.environ.get("TEST_API_BASE_URL", "http://127.0.0.1:4010")


class TestReady:
    parametrize = pytest.mark.parametrize("client", [False, True], indirect=True, ids=["loose", "strict"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_check(self, client: FleetRlmTypescriptSDK) -> None:
        ready = client.ready.check()
        assert_matches_type(ReadyCheckResponse, ready, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_check(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.ready.with_raw_response.check()

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        ready = response.parse()
        assert_matches_type(ReadyCheckResponse, ready, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_check(self, client: FleetRlmTypescriptSDK) -> None:
        with client.ready.with_streaming_response.check() as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            ready = response.parse()
            assert_matches_type(ReadyCheckResponse, ready, path=["response"])

        assert cast(Any, response.is_closed) is True


class TestAsyncReady:
    parametrize = pytest.mark.parametrize(
        "async_client", [False, True, {"http_client": "aiohttp"}], indirect=True, ids=["loose", "strict", "aiohttp"]
    )

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_check(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        ready = await async_client.ready.check()
        assert_matches_type(ReadyCheckResponse, ready, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_check(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.ready.with_raw_response.check()

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        ready = await response.parse()
        assert_matches_type(ReadyCheckResponse, ready, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_check(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.ready.with_streaming_response.check() as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            ready = await response.parse()
            assert_matches_type(ReadyCheckResponse, ready, path=["response"])

        assert cast(Any, response.is_closed) is True
