from dotenv import load_dotenv
from pydantic import BaseModel

from hatchet_sdk.v2 import Hatchet

load_dotenv()

hatchet = Hatchet(debug=True)


class ExampleWorkflowInput(BaseModel):
    message: str


example_workflow = hatchet.declare_workflow(
    name="example-workflow",
    on_events=["example-event"],
    timeout="10m",
    input_validator=ExampleWorkflowInput,
)
