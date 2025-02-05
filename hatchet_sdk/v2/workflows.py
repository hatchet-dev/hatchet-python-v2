import asyncio
from abc import abstractmethod
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    ParamSpec,
    Type,
    TypeGuard,
    TypeVar,
    Union,
    cast,
)

from pydantic import BaseModel, ConfigDict

from hatchet_sdk.context.context import Context
from hatchet_sdk.contracts.workflows_pb2 import (
    ConcurrencyLimitStrategy as ConcurrencyLimitStrategyProto,
)
from hatchet_sdk.contracts.workflows_pb2 import (
    CreateStepRateLimit,
    CreateWorkflowJobOpts,
    CreateWorkflowStepOpts,
    CreateWorkflowVersionOpts,
    DesiredWorkerLabels,
)
from hatchet_sdk.contracts.workflows_pb2 import StickyStrategy as StickyStrategyProto
from hatchet_sdk.contracts.workflows_pb2 import WorkflowConcurrencyOpts, WorkflowKind
from hatchet_sdk.logger import logger

if TYPE_CHECKING:
    from hatchet_sdk.v2 import Hatchet

R = TypeVar("R")
P = ParamSpec("P")


class EmptyModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class StickyStrategy(str, Enum):
    SOFT = "SOFT"
    HARD = "HARD"


class ConcurrencyLimitStrategy(str, Enum):
    CANCEL_IN_PROGRESS = "CANCEL_IN_PROGRESS"
    DROP_NEWEST = "DROP_NEWEST"
    QUEUE_NEWEST = "QUEUE_NEWEST"
    GROUP_ROUND_ROBIN = "GROUP_ROUND_ROBIN"
    CANCEL_NEWEST = "CANCEL_NEWEST"


class ConcurrencyExpression(BaseModel):
    """
    Defines concurrency limits for a workflow using a CEL expression.

    Args:
        expression (str): CEL expression to determine concurrency grouping. (i.e. "input.user_id")
        max_runs (int): Maximum number of concurrent workflow runs.
        limit_strategy (ConcurrencyLimitStrategy): Strategy for handling limit violations.

    Example:
        ConcurrencyExpression("input.user_id", 5, ConcurrencyLimitStrategy.CANCEL_IN_PROGRESS)
    """

    expression: str
    max_runs: int
    limit_strategy: ConcurrencyLimitStrategy


TWorkflowInput = TypeVar("TWorkflowInput", bound=BaseModel, default=EmptyModel)


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str = ""
    on_events: list[str] = []
    on_crons: list[str] = []
    version: str = ""
    timeout: str = "60m"
    schedule_timeout: str = "5m"
    sticky: StickyStrategy | None = None
    default_priority: int = 1
    concurrency: ConcurrencyExpression | None = None
    input_validator: Type[BaseModel] = EmptyModel


class StepType(str, Enum):
    DEFAULT = "default"
    CONCURRENCY = "concurrency"
    ON_FAILURE = "on_failure"


AsyncFunc = Callable[[Any, Context], Awaitable[R]]
SyncFunc = Callable[[Any, Context], R]
StepFunc = Union[AsyncFunc[R], SyncFunc[R]]


def is_async_fn(fn: StepFunc[R]) -> TypeGuard[AsyncFunc[R]]:
    return asyncio.iscoroutinefunction(fn)


def is_sync_fn(fn: StepFunc[R]) -> TypeGuard[SyncFunc[R]]:
    return not asyncio.iscoroutinefunction(fn)


class Step(Generic[R]):
    def __init__(
        self,
        fn: Callable[[Any, Context], R] | Callable[[Any, Context], Awaitable[R]],
        type: StepType,
        name: str = "",
        timeout: str = "60m",
        parents: list[str] = [],
        retries: int = 0,
        rate_limits: list[CreateStepRateLimit] = [],
        desired_worker_labels: dict[str, DesiredWorkerLabels] = {},
        backoff_factor: float | None = None,
        backoff_max_seconds: int | None = None,
        concurrency__max_runs: int | None = None,
        concurrency__limit_strategy: ConcurrencyLimitStrategy | None = None,
    ) -> None:
        self.fn = fn
        self.is_async_function = is_async_fn(fn)

        self.type = type
        self.timeout = timeout
        self.name = name
        self.parents = parents
        self.retries = retries
        self.rate_limits = rate_limits
        self.desired_worker_labels = desired_worker_labels
        self.backoff_factor = backoff_factor
        self.backoff_max_seconds = backoff_max_seconds
        self.concurrency__max_runs = concurrency__max_runs
        self.concurrency__limit_strategy = concurrency__limit_strategy


class RegisteredStep(Generic[R]):
    def __init__(
        self,
        workflow: "BaseWorkflow",
        step: Step[R],
    ) -> None:
        self.workflow = workflow
        self.step = step

    def call(self, ctx: Context) -> R:
        if self.step.is_async_function:
            raise TypeError(
                f"{self.step.name} is not a sync function. Use `acall` instead."
            )

        sync_fn = self.step.fn
        if is_sync_fn(sync_fn):
            return sync_fn(self.workflow, ctx)

        raise TypeError(
            f"{self.step.name} is not a sync function. Use `acall` instead."
        )

    async def acall(self, ctx: Context) -> R:
        if not self.step.is_async_function:
            raise TypeError(
                f"{self.step.name} is not an async function. Use `call` instead."
            )

        async_fn = self.step.fn

        if is_async_fn(async_fn):
            return await async_fn(self.workflow, ctx)

        raise TypeError(
            f"{self.step.name} is not an async function. Use `call` instead."
        )


class WorkflowDeclaration(Generic[TWorkflowInput]):
    def __init__(self, config: WorkflowConfig, hatchet: Union["Hatchet", None]):
        self.config = config
        self.hatchet = hatchet

    def run(self, input: TWorkflowInput | None = None) -> Any:
        if not self.hatchet:
            raise ValueError("Hatchet client is not initialized.")

        return self.hatchet.admin.run_workflow(
            workflow_name=self.config.name, input=input.model_dump() if input else {}
        )

    def workflow_input(self, ctx: Context) -> TWorkflowInput:
        return cast(TWorkflowInput, ctx.workflow_input())


class BaseWorkflow:
    """
    A Hatchet workflow implementation base. This class should be inherited by all workflow implementations.

    Configuration is passed to the workflow implementation via the `config` attribute.
    """

    config: WorkflowConfig = WorkflowConfig()

    def __init__(self) -> None:
        self.config.name = self.config.name or str(self.__class__.__name__)

    def get_service_name(self, namespace: str) -> str:
        return f"{namespace}{self.config.name.lower()}"

    def _get_steps_by_type(self, step_type: StepType) -> list[Step[Any]]:
        return [
            attr
            for _, attr in self.__class__.__dict__.items()
            if isinstance(attr, Step) and attr.type == step_type
        ]

    @property
    def on_failure_steps(self) -> list[Step[Any]]:
        return self._get_steps_by_type(StepType.ON_FAILURE)

    @property
    def concurrency_actions(self) -> list[Step[Any]]:
        return self._get_steps_by_type(StepType.CONCURRENCY)

    @property
    def default_steps(self) -> list[Step[Any]]:
        return self._get_steps_by_type(StepType.DEFAULT)

    @property
    def steps(self) -> list[Step[Any]]:
        return self.default_steps + self.concurrency_actions + self.on_failure_steps

    def create_action_name(self, namespace: str, step: Step[Any]) -> str:
        return self.get_service_name(namespace) + ":" + step.name

    def get_name(self, namespace: str) -> str:
        return namespace + self.config.name

    def validate_concurrency_actions(
        self, service_name: str
    ) -> WorkflowConcurrencyOpts | None:
        if len(self.concurrency_actions) > 0 and self.config.concurrency:
            raise ValueError(
                "Error: Both concurrencyActions and concurrency_expression are defined. Please use only one concurrency configuration method."
            )

        if len(self.concurrency_actions) > 0:
            action = self.concurrency_actions[0]

            return WorkflowConcurrencyOpts(
                action=service_name + ":" + action.name,
                max_runs=action.concurrency__max_runs,
                limit_strategy=cast(
                    str | None,
                    self.validate_concurrency(action.concurrency__limit_strategy),
                ),
            )

        if self.config.concurrency:
            return WorkflowConcurrencyOpts(
                expression=self.config.concurrency.expression,
                max_runs=self.config.concurrency.max_runs,
                limit_strategy=self.config.concurrency.limit_strategy,
            )

        return None

    def validate_on_failure_steps(
        self, name: str, service_name: str
    ) -> CreateWorkflowJobOpts | None:
        if not self.on_failure_steps:
            return None

        on_failure_step = next(iter(self.on_failure_steps))

        return CreateWorkflowJobOpts(
            name=name + "-on-failure",
            steps=[
                CreateWorkflowStepOpts(
                    readable_id=on_failure_step.name,
                    action=service_name + ":" + on_failure_step.name,
                    timeout=on_failure_step.timeout or "60s",
                    inputs="{}",
                    parents=[],
                    retries=on_failure_step.retries,
                    rate_limits=on_failure_step.rate_limits,
                    backoff_factor=on_failure_step.backoff_factor,
                    backoff_max_seconds=on_failure_step.backoff_max_seconds,
                )
            ],
        )

    def validate_priority(self, default_priority: int | None) -> int | None:
        validated_priority = (
            max(1, min(3, default_priority)) if default_priority else None
        )
        if validated_priority != default_priority:
            logger.warning(
                "Warning: Default Priority Must be between 1 and 3 -- inclusively. Adjusted to be within the range."
            )

        return validated_priority

    def validate_concurrency(
        self, concurrency: ConcurrencyLimitStrategy | None
    ) -> int | None:
        if not concurrency:
            return None

        names = [item.name for item in ConcurrencyLimitStrategyProto.DESCRIPTOR.values]

        for name in names:
            if name == concurrency.name:
                return StickyStrategyProto.Value(concurrency.name)

        raise ValueError(
            f"Concurrency limit strategy must be one of {names}. Got: {concurrency}"
        )

    def validate_sticky(self, sticky: StickyStrategy | None) -> int | None:
        if not sticky:
            return None

        names = [item.name for item in StickyStrategyProto.DESCRIPTOR.values]

        for name in names:
            if name == sticky.name:
                return StickyStrategyProto.Value(sticky.name)

        raise ValueError(f"Sticky strategy must be one of {names}. Got: {sticky}")

    def get_create_opts(self, namespace: str) -> CreateWorkflowVersionOpts:
        service_name = self.get_service_name(namespace)

        name = self.get_name(namespace)
        event_triggers = [namespace + event for event in self.config.on_events]

        create_step_opts = [
            CreateWorkflowStepOpts(
                readable_id=step.name,
                action=service_name + ":" + step.name,
                timeout=step.timeout or "60s",
                inputs="{}",
                parents=[x for x in step.parents],
                retries=step.retries,
                rate_limits=step.rate_limits,
                worker_labels=step.desired_worker_labels,
                backoff_factor=step.backoff_factor,
                backoff_max_seconds=step.backoff_max_seconds,
            )
            for step in self.steps
            if step.type == StepType.DEFAULT
        ]

        concurrency = self.validate_concurrency_actions(service_name)
        on_failure_job = self.validate_on_failure_steps(name, service_name)
        validated_priority = self.validate_priority(self.config.default_priority)

        return CreateWorkflowVersionOpts(
            name=name,
            kind=WorkflowKind.DAG,
            version=self.config.version,
            event_triggers=event_triggers,
            cron_triggers=self.config.on_crons,
            schedule_timeout=self.config.schedule_timeout,
            sticky=cast(str | None, self.validate_sticky(self.config.sticky)),
            jobs=[
                CreateWorkflowJobOpts(
                    name=name,
                    steps=create_step_opts,
                )
            ],
            on_failure_job=on_failure_job,
            concurrency=concurrency,
            default_priority=validated_priority,
        )
