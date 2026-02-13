# File generated from our OpenAPI spec by Stainless. See CONTRIBUTING.md for details.

from __future__ import annotations

import httpx

from .._types import Body, Query, Headers, NotGiven, not_given
from .._compat import cached_property
from .._resource import SyncAPIResource, AsyncAPIResource
from .._response import (
    to_raw_response_wrapper,
    to_streamed_response_wrapper,
    async_to_raw_response_wrapper,
    async_to_streamed_response_wrapper,
)
from .._base_client import make_request_options
from ..types.ready_check_response import ReadyCheckResponse

__all__ = ["ReadyResource", "AsyncReadyResource"]


class ReadyResource(SyncAPIResource):
    @cached_property
    def with_raw_response(self) -> ReadyResourceWithRawResponse:
        """
        This property can be used as a prefix for any HTTP method call to return
        the raw response object instead of the parsed content.

        For more information, see https://www.github.com/Qredence/fleet-rlm#accessing-raw-response-data-eg-headers
        """
        return ReadyResourceWithRawResponse(self)

    @cached_property
    def with_streaming_response(self) -> ReadyResourceWithStreamingResponse:
        """
        An alternative to `.with_raw_response` that doesn't eagerly read the response body.

        For more information, see https://www.github.com/Qredence/fleet-rlm#with_streaming_response
        """
        return ReadyResourceWithStreamingResponse(self)

    def check(
        self,
        *,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> ReadyCheckResponse:
        """Ready"""
        return self._get(
            "/ready",
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=ReadyCheckResponse,
        )


class AsyncReadyResource(AsyncAPIResource):
    @cached_property
    def with_raw_response(self) -> AsyncReadyResourceWithRawResponse:
        """
        This property can be used as a prefix for any HTTP method call to return
        the raw response object instead of the parsed content.

        For more information, see https://www.github.com/Qredence/fleet-rlm#accessing-raw-response-data-eg-headers
        """
        return AsyncReadyResourceWithRawResponse(self)

    @cached_property
    def with_streaming_response(self) -> AsyncReadyResourceWithStreamingResponse:
        """
        An alternative to `.with_raw_response` that doesn't eagerly read the response body.

        For more information, see https://www.github.com/Qredence/fleet-rlm#with_streaming_response
        """
        return AsyncReadyResourceWithStreamingResponse(self)

    async def check(
        self,
        *,
        # Use the following arguments if you need to pass additional parameters to the API that aren't available via kwargs.
        # The extra values given here take precedence over values defined on the client or passed to this method.
        extra_headers: Headers | None = None,
        extra_query: Query | None = None,
        extra_body: Body | None = None,
        timeout: float | httpx.Timeout | None | NotGiven = not_given,
    ) -> ReadyCheckResponse:
        """Ready"""
        return await self._get(
            "/ready",
            options=make_request_options(
                extra_headers=extra_headers, extra_query=extra_query, extra_body=extra_body, timeout=timeout
            ),
            cast_to=ReadyCheckResponse,
        )


class ReadyResourceWithRawResponse:
    def __init__(self, ready: ReadyResource) -> None:
        self._ready = ready

        self.check = to_raw_response_wrapper(
            ready.check,
        )


class AsyncReadyResourceWithRawResponse:
    def __init__(self, ready: AsyncReadyResource) -> None:
        self._ready = ready

        self.check = async_to_raw_response_wrapper(
            ready.check,
        )


class ReadyResourceWithStreamingResponse:
    def __init__(self, ready: ReadyResource) -> None:
        self._ready = ready

        self.check = to_streamed_response_wrapper(
            ready.check,
        )


class AsyncReadyResourceWithStreamingResponse:
    def __init__(self, ready: AsyncReadyResource) -> None:
        self._ready = ready

        self.check = async_to_streamed_response_wrapper(
            ready.check,
        )
