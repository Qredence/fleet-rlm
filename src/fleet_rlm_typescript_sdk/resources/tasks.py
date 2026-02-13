# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

from typing import Optional
from typing_extensions import Literal

import httpx

from ..types import task_run_basic_params, task_run_architecture_params, task_run_long_context_params
from .._types import Body, Omit, Query, Headers, NotGiven, omit, not_given
from .._utils import maybe_transform, async_maybe_transform
from .._compat import cached_property
from .._resource import SyncAPIResource, AsyncAPIResource
from .._response import (
    to_raw_response_wrapper,
    to_streamed_response_wrapper,
    async_to_raw_response_wrapper,
    async_to_streamed_response_wrapper,
)
from .._base_client import make_request_options
from ..types.task_response import TaskResponse

__all__ = ["TasksResource", "AsyncTasksResource"]


class TasksResource(SyncAPIResource):
    @cached_property
    def with_raw_response(self) -> TasksResourceWithRawResponse:
        """
        This property can be used as a prefix for any HTTP method call to return
        the raw response object instead of the parsed content.

        For more information, see https://www.github.com/Qredence/fleet-rlm#accessing-raw-response-data-eg-headers
        """
        return TasksResourceWithRawResponse(self)

    @cached_property
    def with_streaming_response(self) -> TasksResourceWithStreamingResponse:
        """
        An alternative to `.with_raw_response` that doesn't eagerly read the response body.

        For more information, see https://www.github.com/Qredence/fleet-rlm#with_streaming_response
        """
        return TasksResourceWithStreamingResponse(self)

    def check_secret(
        self,
        *,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """Check Secret"""
        return self._post(
            "/tasks/check-secret",
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    def run_architecture(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Architecture

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return self._post(
            "/tasks/architecture",
            body=maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_architecture_params.TaskRunArchitectureParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    def run_basic(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Basic

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return self._post(
            "/tasks/basic",
            body=maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_basic_params.TaskRunBasicParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    def run_long_context(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Long Context

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return self._post(
            "/tasks/long-context",
            body=maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_long_context_params.TaskRunLongContextParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )


class AsyncTasksResource(AsyncAPIResource):
    @cached_property
    def with_raw_response(self) -> AsyncTasksResourceWithRawResponse:
        """
        This property can be used as a prefix for any HTTP method call to return
        the raw response object instead of the parsed content.

        For more information, see https://www.github.com/Qredence/fleet-rlm#accessing-raw-response-data-eg-headers
        """
        return AsyncTasksResourceWithRawResponse(self)

    @cached_property
    def with_streaming_response(self) -> AsyncTasksResourceWithStreamingResponse:
        """
        An alternative to `.with_raw_response` that doesn't eagerly read the response body.

        For more information, see https://www.github.com/Qredence/fleet-rlm#with_streaming_response
        """
        return AsyncTasksResourceWithStreamingResponse(self)

    async def check_secret(
        self,
        *,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """Check Secret"""
        return await self._post(
            "/tasks/check-secret",
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    async def run_architecture(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Architecture

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return await self._post(
            "/tasks/architecture",
            body=await async_maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_architecture_params.TaskRunArchitectureParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    async def run_basic(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Basic

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return await self._post(
            "/tasks/basic",
            body=await async_maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_basic_params.TaskRunBasicParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )

    async def run_long_context(
        self,
        *,
        task_type: Literal[
            "basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"
        ],
        chars: int | Omit = omit,
        docs_path: Optional[str] | Omit = omit,
        max_iterations: int | Omit = omit,
        max_llm_calls: int | Omit = omit,
        query: str | Omit = omit,
        question: str | Omit = omit,
        api_timeout: int | Omit = omit,
        verbose: bool | Omit = omit,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> TaskResponse:
        """
        Run Long Context

        Args:
          extra_headers: Send extra headers

          extra_query: Add additional query parameters to the request

          extra_body: Add additional JSON properties to the request

          timeout: Override the client-level default timeout for this request, in seconds
        """
        return await self._post(
            "/tasks/long-context",
            body=await async_maybe_transform(
                {
                    "task_type": task_type,
                    "chars": chars,
                    "docs_path": docs_path,
                    "max_iterations": max_iterations,
                    "max_llm_calls": max_llm_calls,
                    "query": query,
                    "question": question,
                    "api_timeout": api_timeout,
                    "verbose": verbose,
                },
                task_run_long_context_params.TaskRunLongContextParams,
            ),
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=TaskResponse,
        )


class TasksResourceWithRawResponse:
    def __init__(self, tasks: TasksResource) -> None:
        self._tasks = tasks

        self.check_secret = to_raw_response_wrapper(
            tasks.check_secret,
        )
        self.run_architecture = to_raw_response_wrapper(
            tasks.run_architecture,
        )
        self.run_basic = to_raw_response_wrapper(
            tasks.run_basic,
        )
        self.run_long_context = to_raw_response_wrapper(
            tasks.run_long_context,
        )


class AsyncTasksResourceWithRawResponse:
    def __init__(self, tasks: AsyncTasksResource) -> None:
        self._tasks = tasks

        self.check_secret = async_to_raw_response_wrapper(
            tasks.check_secret,
        )
        self.run_architecture = async_to_raw_response_wrapper(
            tasks.run_architecture,
        )
        self.run_basic = async_to_raw_response_wrapper(
            tasks.run_basic,
        )
        self.run_long_context = async_to_raw_response_wrapper(
            tasks.run_long_context,
        )


class TasksResourceWithStreamingResponse:
    def __init__(self, tasks: TasksResource) -> None:
        self._tasks = tasks

        self.check_secret = to_streamed_response_wrapper(
            tasks.check_secret,
        )
        self.run_architecture = to_streamed_response_wrapper(
            tasks.run_architecture,
        )
        self.run_basic = to_streamed_response_wrapper(
            tasks.run_basic,
        )
        self.run_long_context = to_streamed_response_wrapper(
            tasks.run_long_context,
        )


class AsyncTasksResourceWithStreamingResponse:
    def __init__(self, tasks: AsyncTasksResource) -> None:
        self._tasks = tasks

        self.check_secret = async_to_streamed_response_wrapper(
            tasks.check_secret,
        )
        self.run_architecture = async_to_streamed_response_wrapper(
            tasks.run_architecture,
        )
        self.run_basic = async_to_streamed_response_wrapper(
            tasks.run_basic,
        )
        self.run_long_context = async_to_streamed_response_wrapper(
            tasks.run_long_context,
        )
