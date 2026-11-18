"""Bodyful classifier steps, calling their decision model through a provider adapter."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import update_wrapper

from pydantic import BaseModel, JsonValue, PydanticUserError, TypeAdapter, ValidationError

from ..dag import Node, NodeType, _safe_issubclass
from ..policies import StepPolicy, UncertaintyPolicy
from ..step_interface import (
    decoration_namespace,
    resolve_step_signature,
    step_interface_from_signature,
)
from . import adapter as provider_adapter
from .errors import ClassifierStepError, ClassifierStepExecutionError
from .evidence import emit_classifier_evidence
from .models import (
    ClassificationResult,
    ClassifierDeclaration,
    ClassifierInvocation,
    ClassifierSettings,
    JSONContent,
    resolve_runtime,
    validate_questions,
    validate_runtime_defaults,
    validate_state,
)
from .providers import ClassifierProvider

__all__ = [
    "Classifier",
    "ClassifierStepError",
    "ClassifierStepExecutionError",
    "classifier_step",
]

_WORKFLOW_CLASSIFIER_DEFAULTS: ContextVar[Mapping[str, JsonValue]] = ContextVar(
    "avalanche_classifier_workflow_defaults", default={}
)


def _error_description(error: BaseException) -> str:
    """Describe failures without leaking request state or server-echoed secrets into errors."""
    if isinstance(error, (ClassifierStepError, ClassifierStepExecutionError)):
        # Raised by this package or a provider adapter, which keep them payload-free.
        return str(error)
    # Validation messages and locations can also contain input values or keys.
    return type(error).__name__


class Classifier:
    """Injected callable retaining each validated request state in lifecycle evidence."""

    def __init__(
        self,
        *,
        declaration: ClassifierDeclaration,
        step_name: str,
        input_model: type[BaseModel] | None = None,
    ) -> None:
        self._declaration = declaration
        self._step_name = step_name
        self._input_model = input_model
        self._adapter = provider_adapter.adapter_for(declaration.runtime.classifier)
        self._connection: provider_adapter.ClassifierConnection | None = None
        self._invocation_index = 0
        self._closed = False
        self._pending: set[asyncio.Task[object]] = set()

    async def __call__(
        self, *, state: JSONContent, questions: dict[str, JsonValue] | None = None
    ) -> ClassificationResult:
        if self._closed:
            raise ClassifierStepError("classifier cannot be used after its step has finished")
        invocation_index = self._invocation_index
        self._invocation_index += 1
        started_at = time.time()
        record = ClassifierInvocation(
            invocation_id=uuid.uuid4().hex,
            invocation_index=invocation_index,
            status="running",
            started_at=started_at,
            ended_at=None,
            declaration=self._declaration.model_copy(update={"questions": None}),
            input=None,
            result=None,
            error=None,
        )
        task = asyncio.current_task()
        if task is not None:
            self._pending.add(task)
        try:
            try:
                try:
                    resolved_questions = (
                        self._declaration.questions
                        if questions is None
                        else validate_questions(questions)
                    )
                    if resolved_questions is None:
                        raise ClassifierStepError(
                            "questions must be provided on the classifier call "
                            "or as classifier_step defaults"
                        )
                    declaration = self._declaration.model_copy(
                        update={"questions": deepcopy(resolved_questions)}
                    )
                    record = record.model_copy(update={"declaration": declaration})
                    request_state = validate_state(state)
                    if self._input_model is not None:
                        validated = self._input_model.model_validate_json(
                            json.dumps(request_state, ensure_ascii=False, allow_nan=False)
                        )
                        request_state = validate_state(
                            validated.model_dump(mode="json", by_alias=True)
                        )
                    record = record.model_copy(update={"input": deepcopy(request_state)})
                finally:
                    # Invalid requests still get a running/failed evidence pair.
                    emit_classifier_evidence(record)
                self._adapter.check_questions(resolved_questions)
                if self._connection is None:
                    self._connection = provider_adapter.connect(
                        self._adapter, self._declaration.runtime
                    )
                result = await provider_adapter.classify(
                    self._adapter,
                    self._connection,
                    state=request_state,
                    declaration=declaration,
                )
            except BaseException as error:
                status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
                description = _error_description(error)
                terminal = record.model_copy(
                    update={
                        "status": status,
                        "ended_at": max(started_at, time.time()),
                        "error": description,
                    }
                )
                failure = (
                    ClassifierStepExecutionError(
                        f"classifier step {self._step_name!r} failed: {description}"
                    )
                    if isinstance(error, Exception)
                    else error
                )
                try:
                    emit_classifier_evidence(terminal)
                except BaseException as evidence_error:
                    failure.add_note(
                        "Classifier evidence capture failed: "
                        f"{_error_description(evidence_error)}"
                    )
                if not isinstance(error, Exception):
                    raise
                raise failure from None
            emit_classifier_evidence(
                record.model_copy(
                    update={
                        "status": "success",
                        "ended_at": max(started_at, time.time()),
                        "result": result,
                    }
                )
            )
            return result
        finally:
            if task is not None:
                self._pending.discard(task)

    async def aclose(self) -> None:
        """Finish outstanding calls and close the single step-owned provider client."""
        if self._closed:
            return
        self._closed = True
        pending = tuple(self._pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._connection is not None:
            await self._connection.aclose()


async def _close_classifier(classifier: Classifier) -> None:
    # Finish cleanup even if another cancellation arrives while closing sockets.
    closing = asyncio.create_task(classifier.aclose())
    try:
        await asyncio.shield(closing)
    except asyncio.CancelledError as cancellation:
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            closing.result()
        except BaseException as error:
            cancellation.add_note(f"Classifier cleanup failed: {_error_description(error)}")
        raise


@dataclass(frozen=True)
class _ClassifierStepSpec:
    user_fn: Callable[..., object]
    declaration_json: str
    input_model: type[BaseModel] | None
    settings: ClassifierSettings
    public_signature: inspect.Signature

    def declaration(
        self, workflow_defaults: Mapping[str, JsonValue] | None = None
    ) -> ClassifierDeclaration:
        runtime = resolve_runtime(
            ClassifierSettings.model_validate(dict(workflow_defaults or {})), self.settings
        )
        declaration = ClassifierDeclaration.model_validate_json(self.declaration_json)
        if declaration.questions is not None:
            provider_adapter.adapter_for(runtime.classifier).check_questions(
                declaration.questions
            )
        return declaration.model_copy(update={"runtime": runtime})

    def declaration_metadata(
        self, workflow_defaults: Mapping[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        """Return an owned declaration without consulting credentials or the network."""
        return self.declaration(workflow_defaults).model_dump(mode="json")

    def make_classifier(self) -> Classifier:
        return Classifier(
            declaration=self.declaration(_WORKFLOW_CLASSIFIER_DEFAULTS.get()),
            step_name=self.user_fn.__qualname__,
            input_model=self.input_model,
        )

    def with_workflow_defaults(
        self, fn: Callable[..., object], defaults: Mapping[str, JsonValue]
    ) -> Callable[..., object]:
        owned_defaults = validate_runtime_defaults(dict(defaults))

        async def bound(*args: object, **kwargs: object) -> object:
            # Cloudpickle must resolve this ContextVar in the worker, not capture it.
            from avalanche.classifier.classifier_step import _WORKFLOW_CLASSIFIER_DEFAULTS

            token = _WORKFLOW_CLASSIFIER_DEFAULTS.set(owned_defaults)
            try:
                result = fn(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result
            finally:
                _WORKFLOW_CLASSIFIER_DEFAULTS.reset(token)

        update_wrapper(bound, fn)
        bound.__signature__ = self.public_signature  # type: ignore[attr-defined]
        return bound


def _public_step_signature(
    user_fn: Callable[..., object], localns: dict[str, object] | None
) -> inspect.Signature:
    signature = resolve_step_signature(user_fn, localns)
    parameter = signature.parameters.get("classifier")
    if parameter is None or parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
        raise ClassifierStepError(
            f"classifier step {user_fn.__qualname__!r} requires "
            "a keyword-only classifier parameter"
        )
    if parameter.default is not inspect.Parameter.empty:
        raise ClassifierStepError("classifier is framework-injected and cannot have a default")
    if parameter.annotation is not Classifier:
        raise ClassifierStepError("classifier parameter must be annotated ava.Classifier")
    return signature.replace(
        parameters=[
            value for name, value in signature.parameters.items() if name != "classifier"
        ],
    )


def classifier_step(
    *,
    questions: dict[str, JsonValue] | None = None,
    input_model: type[BaseModel] | None = None,
    classifier: ClassifierProvider | None = None,
    model: str | None = None,
    timeout: float | None = None,
    slug: str | None = None,
    retries: int = 7,
    on_uncertain: UncertaintyPolicy = "retry",
) -> Callable[[Callable[..., object]], Node]:
    """Declare a bodyful classifier step with optional default questions.

    ``classifier`` picks the provider (``ava.classifiers.TypeSafe()`` by default) and
    ``model`` one of its models. Both win over the workflow's ``classifier_defaults``.
    """
    try:
        validated_questions = None if questions is None else validate_questions(questions)
        if input_model is not None and not _safe_issubclass(input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic model class")
        input_schema = (
            TypeAdapter(input_model).json_schema(mode="serialization")
            if input_model is not None
            else None
        )
        overrides: dict[str, object] = {}
        if classifier is not None:
            overrides["classifier"] = classifier
        if model is not None:
            overrides["model"] = model
        if timeout is not None:
            overrides["timeout"] = timeout
        settings = ClassifierSettings.model_validate(overrides)
    except (PydanticUserError, ValidationError, ValueError, TypeError) as error:
        raise ClassifierStepError(
            f"invalid classifier declaration: {_error_description(error)}"
        ) from None

    if settings.classifier is not None and validated_questions is not None:
        # The provider is known here; a workflow default is checked with the workflow.
        provider_adapter.adapter_for(settings.classifier).check_questions(validated_questions)

    def decorator(user_fn: Callable[..., object]) -> Node:
        public_signature = _public_step_signature(user_fn, decoration_namespace())
        interface = step_interface_from_signature(public_signature)
        declaration = ClassifierDeclaration(
            questions=validated_questions,
            runtime=resolve_runtime(settings),
            input_schema=input_schema,
            step_inputs=interface.step_inputs,
            step_output=interface.step_output,
        )
        spec = _ClassifierStepSpec(
            user_fn=user_fn,
            declaration_json=declaration.model_dump_json(),
            input_model=input_model,
            settings=settings,
            public_signature=public_signature,
        )

        async def wrapper(*args: object, **kwargs: object) -> object:
            if "classifier" in kwargs:
                raise ClassifierStepError(
                    "classifier is framework-injected and cannot be overridden"
                )
            classifier = spec.make_classifier()
            primary_error: BaseException | None = None
            try:
                result = user_fn(*args, **kwargs, classifier=classifier)
                return await result if inspect.isawaitable(result) else result
            except BaseException as error:
                primary_error = error
                raise
            finally:
                try:
                    await _close_classifier(classifier)
                except BaseException as close_error:
                    if primary_error is not None:
                        primary_error.add_note(
                            f"Classifier cleanup failed: {_error_description(close_error)}"
                        )
                    elif isinstance(close_error, Exception):
                        raise ClassifierStepExecutionError(
                            f"classifier cleanup failed: {_error_description(close_error)}"
                        ) from None
                    else:
                        raise

        update_wrapper(wrapper, user_fn)
        wrapper.__signature__ = spec.public_signature  # type: ignore[attr-defined]
        wrapper.__classifier_step__ = spec  # type: ignore[attr-defined]
        return Node(
            wrapper,
            NodeType.STEP,
            num_returns=1,
            slug=slug,
            step_interface=interface,
            policy=StepPolicy(retries=retries, on_uncertain=on_uncertain),
        )

    return decorator
