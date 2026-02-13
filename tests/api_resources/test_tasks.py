# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from tests.utils import assert_matches_type
from fleet_rlm_typescript_sdk import FleetRlmTypescriptSDK, AsyncFleetRlmTypescriptSDK
from fleet_rlm_typescript_sdk.types import (
    TaskResponse,
)

base_url = os.environ.get("TEST_API_BASE_URL", "http://127.0.0.1:4010")


class TestTasks:
    parametrize = pytest.mark.parametrize("client", [False, True], indirect=True, ids=["loose", "strict"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_check_secret(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.check_secret()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_check_secret(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.tasks.with_raw_response.check_secret()

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_check_secret(self, client: FleetRlmTypescriptSDK) -> None:
        with client.tasks.with_streaming_response.check_secret() as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_architecture(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_architecture(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_architecture_with_all_params(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_architecture(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_run_architecture(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.tasks.with_raw_response.run_architecture(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_run_architecture(self, client: FleetRlmTypescriptSDK) -> None:
        with client.tasks.with_streaming_response.run_architecture(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_basic(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_basic(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_basic_with_all_params(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_basic(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_run_basic(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.tasks.with_raw_response.run_basic(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_run_basic(self, client: FleetRlmTypescriptSDK) -> None:
        with client.tasks.with_streaming_response.run_basic(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_long_context(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_long_context(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_method_run_long_context_with_all_params(self, client: FleetRlmTypescriptSDK) -> None:
        task = client.tasks.run_long_context(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_raw_response_run_long_context(self, client: FleetRlmTypescriptSDK) -> None:
        response = client.tasks.with_raw_response.run_long_context(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    def test_streaming_response_run_long_context(self, client: FleetRlmTypescriptSDK) -> None:
        with client.tasks.with_streaming_response.run_long_context(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True


class TestAsyncTasks:
    parametrize = pytest.mark.parametrize(
        "async_client", [False, True, {"http_client": "aiohttp"}], indirect=True, ids=["loose", "strict", "aiohttp"]
    )

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_check_secret(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.check_secret()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_check_secret(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.tasks.with_raw_response.check_secret()

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = await response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_check_secret(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.tasks.with_streaming_response.check_secret() as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = await response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_architecture(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_architecture(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_architecture_with_all_params(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_architecture(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_run_architecture(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.tasks.with_raw_response.run_architecture(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = await response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_run_architecture(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.tasks.with_streaming_response.run_architecture(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = await response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_basic(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_basic(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_basic_with_all_params(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_basic(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_run_basic(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.tasks.with_raw_response.run_basic(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = await response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_run_basic(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.tasks.with_streaming_response.run_basic(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = await response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_long_context(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_long_context(
            task_type="basic",
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_method_run_long_context_with_all_params(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        task = await async_client.tasks.run_long_context(
            task_type="basic",
            chars=0,
            docs_path="docs_path",
            max_iterations=0,
            max_llm_calls=0,
            query="query",
            question="question",
            api_timeout=0,
            verbose=True,
        )
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_raw_response_run_long_context(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        response = await async_client.tasks.with_raw_response.run_long_context(
            task_type="basic",
        )

        assert response.is_closed is True
        assert response.http_request.headers.get("X-Stainless-Lang") == "python"
        task = await response.parse()
        assert_matches_type(TaskResponse, task, path=["response"])

    @pytest.mark.skip(reason="Prism tests are disabled")
    @parametrize
    async def test_streaming_response_run_long_context(self, async_client: AsyncFleetRlmTypescriptSDK) -> None:
        async with async_client.tasks.with_streaming_response.run_long_context(
            task_type="basic",
        ) as response:
            assert not response.is_closed
            assert response.http_request.headers.get("X-Stainless-Lang") == "python"

            task = await response.parse()
            assert_matches_type(TaskResponse, task, path=["response"])

        assert cast(Any, response.is_closed) is True
