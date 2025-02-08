from hatchet_sdk import Hatchet

hatchet = Hatchet()


async def create_cron() -> None:
    # ❓ Create
    cron_trigger = await hatchet.cron.acreate(
        workflow_name="simple-cron-workflow",
        cron_name="customer-a-daily-report",
        expression="0 12 * * *",
        input={
            "name": "John Doe",
        },
        additional_metadata={
            "customer_id": "customer-a",
        },
    )

    cron_trigger.metadata.id  # the id of the cron trigger
    # !!

    # ❓ List
    await hatchet.cron.alist()
    # !!

    # ❓ Get
    cron_trigger = await hatchet.cron.aget(cron_trigger=cron_trigger.metadata.id)
    # !!

    # ❓ Delete
    await hatchet.cron.adelete(cron_trigger=cron_trigger.metadata.id)
    # !!
