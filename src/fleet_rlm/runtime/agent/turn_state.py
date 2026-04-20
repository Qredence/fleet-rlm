"""Per-turn delegation state — extracted to break circular imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TurnDelegationState:
    """Mutable per-turn counters for ReAct-to-RLM delegation behavior."""

    effective_max_iters: int = 1
    delegate_calls_turn: int = 0
    runtime_module_calls_turn: int = 0
    recursive_delegate_calls_turn: int = 0
    delegate_fallback_count_turn: int = 0
    delegate_result_truncated_count_turn: int = 0

    def reset(self, *, effective_max_iters: int) -> int:
        self.delegate_calls_turn = 0
        self.runtime_module_calls_turn = 0
        self.recursive_delegate_calls_turn = 0
        self.delegate_fallback_count_turn = 0
        self.delegate_result_truncated_count_turn = 0
        self.effective_max_iters = max(1, int(effective_max_iters))
        return self.effective_max_iters

    def claim_runtime_module_slot(self, *, max_calls_per_turn: int) -> tuple[bool, int]:
        limit = max(1, int(max_calls_per_turn))
        if self.runtime_module_calls_turn >= limit:
            return False, limit
        self.runtime_module_calls_turn += 1
        self.delegate_calls_turn += 1
        return True, limit

    def claim_recursive_delegate_slot(
        self, *, max_calls_per_turn: int
    ) -> tuple[bool, int]:
        limit = max(1, int(max_calls_per_turn))
        if self.recursive_delegate_calls_turn >= limit:
            return False, limit
        self.recursive_delegate_calls_turn += 1
        self.delegate_calls_turn += 1
        return True, limit

    def record_fallback(self) -> None:
        self.delegate_fallback_count_turn += 1

    def record_truncation(self) -> None:
        self.delegate_result_truncated_count_turn += 1

    def as_payload(self) -> dict[str, int]:
        return {
            "effective_max_iters": int(self.effective_max_iters),
            "delegate_calls_turn": int(self.delegate_calls_turn),
            "runtime_module_calls_turn": int(self.runtime_module_calls_turn),
            "recursive_delegate_calls_turn": int(self.recursive_delegate_calls_turn),
            "delegate_fallback_count_turn": int(self.delegate_fallback_count_turn),
            "delegate_result_truncated_count_turn": int(
                self.delegate_result_truncated_count_turn
            ),
        }
