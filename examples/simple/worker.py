from hatchet_sdk import BaseWorkflow, Context, Hatchet

from hatchet_sdk import Context
from hatchet_sdk.v2 import Hatchet

hatchet = Hatchet(debug=True)


@hatchet.function()
def step1(context: Context) -> dict[str, str]:
    message = "Hello from Hatchet!"

    context.log(message)

    return {"message": message}


def main() -> None:
    worker = hatchet.worker("test-worker", max_runs=1)
    worker.register_function(step1)
    worker.start()


if __name__ == "__main__":
    main()
