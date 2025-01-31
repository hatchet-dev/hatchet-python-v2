import asyncio
import base64
import json
import os

from dotenv import load_dotenv

from hatchet_sdk import new_client
from hatchet_sdk.clients.admin import TriggerWorkflowOptions
from hatchet_sdk.clients.events import PushEventOptions
from hatchet_sdk.clients.rest.models.workflow_run import WorkflowRun
from hatchet_sdk.clients.run_event_listener import StepRunEventType


async def main() -> None:
    load_dotenv()
    hatchet = new_client()

    hatchet.event.push(
        "parent:create",
        {"n": 999},
        PushEventOptions(additional_metadata={"no-dedupe": "world"}),
    )


if __name__ == "__main__":
    asyncio.run(main())
