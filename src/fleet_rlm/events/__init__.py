"""Canonical semantic Turn event vocabulary (P24).

One authoritative schema consumed by thin live and durable wire adapters;
both SSE streaming and Session reload project onto it so clients share one
reducer instead of one projector per wire format.
"""
