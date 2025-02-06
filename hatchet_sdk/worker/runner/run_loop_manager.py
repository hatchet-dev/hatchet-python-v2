import asyncio
import logging
from dataclasses import dataclass, field
from multiprocessing import Queue
from typing import TYPE_CHECKING, Callable, Literal, TypeVar

from hatchet_sdk import Context
from hatchet_sdk.client import Client, new_client_raw
from hatchet_sdk.clients.dispatcher.action_listener import Action
from hatchet_sdk.loader import ClientConfig
from hatchet_sdk.logger import logger
from hatchet_sdk.utils.types import WorkflowValidator
from hatchet_sdk.worker.action_listener_process import ActionEvent
from hatchet_sdk.worker.runner.runner import Runner
from hatchet_sdk.worker.runner.utils.capture_logs import capture_logs

if TYPE_CHECKING:
    from hatchet_sdk.workflow import Step

STOP_LOOP_TYPE = Literal["STOP_LOOP"]
STOP_LOOP: STOP_LOOP_TYPE = "STOP_LOOP"

T = TypeVar("T")
from typing import Any


@dataclass
class WorkerActionRunLoopManager:
    name: str
    action_registry: dict[str, "Step[Any]"]
    validator_registry: dict[str, WorkflowValidator]
    max_runs: int | None
    config: ClientConfig
    action_queue: "Queue[Action | STOP_LOOP_TYPE]"
    event_queue: "Queue[ActionEvent]"
    loop: asyncio.AbstractEventLoop
    handle_kill: bool = True
    debug: bool = False
    labels: dict[str, str | int] = field(default_factory=dict)

    client: Client = field(init=False)

    killing: bool = field(init=False, default=False)
    runner: Runner | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.debug:
            logger.setLevel(logging.DEBUG)
        self.client = new_client_raw(self.config, self.debug)
        self.start()

    def start(self, retry_count: int = 1) -> None:
        k = self.loop.create_task(self.async_start(retry_count))

    async def async_start(self, retry_count: int = 1) -> None:
        await capture_logs(
            self.client.logInterceptor,
            self.client.event,
            self._async_start,
        )(retry_count=retry_count)

    async def _async_start(self, retry_count: int = 1) -> None:
        logger.info("starting runner...")
        self.loop = asyncio.get_running_loop()
        # needed for graceful termination
        k = self.loop.create_task(self._start_action_loop())
        await k

    def cleanup(self) -> None:
        self.killing = True

        ## TODO: The action queue is a queue of `Action`, so I don't think this will work
        self.action_queue.put(STOP_LOOP)

    async def wait_for_tasks(self) -> None:
        if self.runner:
            await self.runner.wait_for_tasks()

    async def _start_action_loop(self) -> None:
        self.runner = Runner(
            self.name,
            self.event_queue,
            self.max_runs,
            self.handle_kill,
            self.action_registry,
            self.validator_registry,
            self.config,
            self.labels,
        )

        logger.debug(f"'{self.name}' waiting for {list(self.action_registry.keys())}")
        while not self.killing:
            action = await self._get_action()
            ## TODO: This is a queue of `Action`, so I don't think this will work
            if action == STOP_LOOP:
                logger.debug("stopping action runner loop...")
                break

            self.runner.run(action)
        logger.debug("action runner loop stopped")

    async def _get_action(self) -> Action | STOP_LOOP_TYPE:
        return await self.loop.run_in_executor(None, self.action_queue.get)

    async def exit_gracefully(self) -> None:
        if self.killing:
            return

        logger.info("gracefully exiting runner...")

        self.cleanup()

        # Wait for 1 second to allow last calls to flush. These are calls which have been
        # added to the event loop as callbacks to tasks, so we're not aware of them in the
        # task list.
        await asyncio.sleep(1)

    def exit_forcefully(self) -> None:
        logger.info("forcefully exiting runner...")
        self.cleanup()
