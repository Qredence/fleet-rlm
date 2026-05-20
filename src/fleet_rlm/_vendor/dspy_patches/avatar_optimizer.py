"""Patched ``dspy.teleprompt.avatar_optimizer`` without deprecated field args.

This is a minimal source-compatible copy of DSPy 3.2.1's Avatar optimizer with
only two behavior changes:

1. It avoids deprecated ``prefix=`` arguments in signature field declarations.
2. It defines a local ``ActionOutput`` model instead of importing avatar
   signature models so importing the optimizer avoids deprecated field warnings.

The Fleet codebase does not rely on ``AvatarOptimizer`` directly, but DSPy's
package root imports this module eagerly, so keeping it warning-free removes the
deprecation noise from every plain ``import dspy``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from random import sample
from typing import Any, Callable

from dspy.dsp.utils.settings import settings
from dspy.predict.predict import Predict
from dspy.primitives.example import Example
from dspy.primitives.module import Module
from dspy.signatures import InputField, OutputField, Signature
from dspy.teleprompt.teleprompt import Teleprompter
from pydantic import BaseModel
from tqdm import tqdm

DEFAULT_MAX_EXAMPLES = 10


class EvalResult(BaseModel):
    example: dict[str, Any]
    score: float
    actions: list[ActionOutput] | None = None


class ActionOutput(BaseModel):
    tool_name: str
    tool_input_query: str
    tool_output: str


class Comparator(Signature):
    """Compare positive and negative outcomes and suggest tool-use improvements."""

    instruction: str = InputField(desc="Instruction for the actor to execute the task")
    actions: list[str] = InputField(desc="Actions actor can take to complete the task")
    pos_input_with_metrics: list[EvalResult] = InputField(
        desc="Positive inputs along with their score on an evaluation metric and actions taken"
    )
    neg_input_with_metrics: list[EvalResult] = InputField(
        desc="Negative inputs along with their score on an evaluation metric and actions taken"
    )
    feedback: str = OutputField(desc="Feedback for the actor to improve the performance of negative inputs")


class FeedbackBasedInstruction(Signature):
    """Generate a revised instruction that incorporates optimization feedback."""

    previous_instruction: str = InputField(desc="Previous instruction for the actor to execute the task")
    feedback: str = InputField(desc="Feedback for the actor to improve the performance of negative inputs")
    new_instruction: str = OutputField(desc="New instruction for the actor to execute the task")


class AvatarOptimizer(Teleprompter):
    def __init__(
        self,
        metric: Callable[..., float],
        max_iters: int = 10,
        lower_bound: int = 0,
        upper_bound: int = 1,
        max_positive_inputs: int | None = None,
        max_negative_inputs: int | None = None,
        optimize_for: str = "max",
    ) -> None:
        assert metric is not None, "`metric` argument cannot be None. Please provide a metric function."
        self.metric = metric
        self.optimize_for = optimize_for

        self.max_iters = max_iters
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound

        self.max_positive_inputs = max_positive_inputs or DEFAULT_MAX_EXAMPLES
        self.max_negative_inputs = max_negative_inputs or DEFAULT_MAX_EXAMPLES

        self.comparator = Predict(Comparator)
        self.feedback_instruction = Predict(FeedbackBasedInstruction)

    def process_example(self, actor: Any, example: Example, return_outputs: bool):
        actor = deepcopy(actor)

        try:
            prediction = actor(**example.inputs().toDict())
            score = self.metric(example, prediction)

            if return_outputs:
                return example, prediction, score
            return score

        except Exception as exc:
            print(exc)

            if return_outputs:
                return example, None, 0
            return 0

    def thread_safe_evaluator(self, devset, actor, return_outputs: bool = False, num_threads: int | None = None):
        total_score = 0
        total_examples = len(devset)
        results = []
        num_threads = num_threads or settings.num_threads

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(self.process_example, actor, example, return_outputs) for example in devset]

            for future in tqdm(futures, total=total_examples, desc="Processing examples"):
                result = future.result()
                if return_outputs:
                    example, prediction, score = result
                    total_score += score
                    results.append((example, prediction, score))
                else:
                    total_score += result

        avg_metric = total_score / total_examples

        if return_outputs:
            return avg_metric, results
        return avg_metric

    def _get_pos_neg_results(
        self,
        actor: Module,
        trainset: list[Example],
    ) -> tuple[float, list[EvalResult], list[EvalResult]]:
        pos_inputs: list[EvalResult] = []
        neg_inputs: list[EvalResult] = []

        avg_score, results = self.thread_safe_evaluator(trainset, actor, return_outputs=True)
        print(f"Average Score: {avg_score}")

        for example, prediction, score in results:
            if score >= self.upper_bound:
                pos_inputs.append(
                    EvalResult(
                        example=example.inputs().toDict(),
                        score=score,
                        actions=prediction.actions if prediction else None,
                    )
                )
            elif score <= self.lower_bound:
                neg_inputs.append(
                    EvalResult(
                        example=example.inputs().toDict(),
                        score=score,
                        actions=prediction.actions if prediction else None,
                    )
                )

        if len(pos_inputs) == 0:
            raise ValueError("No positive examples found, try lowering the upper_bound or providing more training data")
        if len(neg_inputs) == 0:
            raise ValueError("No negative examples found, try raising the lower_bound or providing more training data")

        return avg_score, pos_inputs, neg_inputs

    def compile(
        self,
        student: Module,
        *,
        trainset: list[Example],
        teacher: Module | None = None,
        valset: list[Example] | None = None,
        **kwargs: Any,
    ) -> Module:
        _ = teacher, valset, kwargs
        best_actor: Any = deepcopy(student)
        best_score = -999 if self.optimize_for == "max" else 999

        for i in range(self.max_iters):
            print(20 * "=")
            print(f"Iteration {i + 1}/{self.max_iters}")

            score, pos_inputs, neg_inputs = self._get_pos_neg_results(best_actor, trainset)
            print(f"Positive examples: {len(pos_inputs)}")
            print(f"Negative examples: {len(neg_inputs)}")
            print(
                f"Sampling {self.max_positive_inputs} positive examples and {self.max_negative_inputs} negative examples"
            )

            if self.max_positive_inputs and len(pos_inputs) > self.max_positive_inputs:
                pos_inputs = sample(pos_inputs, self.max_positive_inputs)

            if self.max_negative_inputs and len(neg_inputs) > self.max_negative_inputs:
                neg_inputs = sample(neg_inputs, self.max_negative_inputs)

            feedback = self.comparator(
                instruction=best_actor.actor.signature.instructions,
                actions=[str(tool) for tool in best_actor.tools],
                pos_input_with_metrics=pos_inputs,
                neg_input_with_metrics=neg_inputs,
            ).feedback

            new_instruction = self.feedback_instruction(
                previous_instruction=best_actor.actor.signature.instructions,
                feedback=feedback,
            ).new_instruction

            print(f"Generated new instruction: {new_instruction}")

            if (self.optimize_for == "max" and best_score < score) or (
                self.optimize_for == "min" and best_score > score
            ):
                best_actor.actor.signature = best_actor.actor.signature.with_instructions(new_instruction)
                best_actor.actor_clone = deepcopy(best_actor.actor)
                best_score = score

        return best_actor


__all__ = [
    "AvatarOptimizer",
    "ActionOutput",
    "Comparator",
    "DEFAULT_MAX_EXAMPLES",
    "EvalResult",
    "FeedbackBasedInstruction",
]
