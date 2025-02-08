from datetime import datetime, timedelta

from hatchet_sdk import Hatchet

hatchet = Hatchet()


async def create_scheduled() -> None:
    # ❓ Create
    scheduled_run = await hatchet.scheduled.acreate(
        workflow_name="simple-workflow",
        trigger_at=datetime.now() + timedelta(seconds=10),
        input={
            "data": "simple-workflow-data",
        },
        additional_metadata={
            "customer_id": "customer-a",
        },
    )

    scheduled_run.metadata.id  # the id of the scheduled run trigger
    # !!

    # ❓ Delete
    await hatchet.scheduled.adelete(scheduled=scheduled_run.metadata.id)
    # !!

    # ❓ List
    await hatchet.scheduled.alist()
    # !!

    # ❓ Get
    scheduled_run = await hatchet.scheduled.aget(scheduled=scheduled_run.metadata.id)
    # !!
