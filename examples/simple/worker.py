from hatchet_sdk import BaseWorkflow, Context, Hatchet

from hatchet_sdk import Context
from hatchet_sdk.v2 import Hatchet

hatchet = Hatchet(debug=True)


@hatchet.function(timeout="11s")
def step1(context: Context) -> dict[str, str]:
    print("executed step1")
    return {
        "step1": "step1",
    }


def main() -> None:
    worker = hatchet.worker("test-worker", max_runs=1)
    worker.register_function(step1)
    worker.start()


if __name__ == "__main__":
    main()
