import asyncio
import datetime
import json
from typing import Any, List, cast

import grpc
from google.protobuf import timestamp_pb2
from pydantic import BaseModel, Field

from hatchet_sdk.clients.rest.tenacity_utils import tenacity_retry
from hatchet_sdk.contracts.events_pb2 import (
    BulkPushEventRequest,
    Event,
    PushEventRequest,
    PutLogRequest,
    PutStreamEventRequest,
)
from hatchet_sdk.contracts.events_pb2_grpc import EventsServiceStub
from hatchet_sdk.loader import ClientConfig
from hatchet_sdk.metadata import get_metadata
from hatchet_sdk.utils.types import JSONSerializableDict


def new_event(conn: grpc.Channel, config: ClientConfig) -> "EventClient":
    return EventClient(
        client=EventsServiceStub(conn),  # type: ignore[no-untyped-call]
        config=config,
    )


def proto_timestamp_now() -> timestamp_pb2.Timestamp:
    t = datetime.datetime.now().timestamp()
    seconds = int(t)
    nanos = int(t % 1 * 1e9)

    return timestamp_pb2.Timestamp(seconds=seconds, nanos=nanos)


class PushEventOptions(BaseModel):
    additional_metadata: JSONSerializableDict = Field(default_factory=dict)
    namespace: str | None = None


class BulkPushEventOptions(BaseModel):
    namespace: str | None = None


class BulkPushEventWithMetadata(BaseModel):
    key: str
    payload: Any
    additional_metadata: JSONSerializableDict = Field(default_factory=dict)


class EventClient:
    def __init__(self, client: EventsServiceStub, config: ClientConfig):
        self.client = client
        self.token = config.token
        self.namespace = config.namespace

    async def aio_push(
        self,
        event_key: str,
        payload: dict[str, Any],
        options: PushEventOptions = PushEventOptions(),
    ) -> Event:
        return await asyncio.to_thread(
            self.push, event_key=event_key, payload=payload, options=options
        )

    async def aio_bulk_push(
        self,
        events: list[BulkPushEventWithMetadata],
        options: BulkPushEventOptions = BulkPushEventOptions(),
    ) -> List[Event]:
        return await asyncio.to_thread(self.bulk_push, events=events, options=options)

    ## IMPORTANT: Keep this method's signature in sync with the wrapper in the OTel instrumentor
    @tenacity_retry
    def push(
        self,
        event_key: str,
        payload: dict[str, Any],
        options: PushEventOptions = PushEventOptions(),
    ) -> Event:
        namespace = options.namespace or self.namespace
        namespaced_event_key = namespace + event_key

        try:
            meta = options.additional_metadata
            meta_bytes = None if meta is None else json.dumps(meta)
        except Exception as e:
            raise ValueError(f"Error encoding meta: {e}")

        try:
            payload_str = json.dumps(payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Error encoding payload: {e}")

        request = PushEventRequest(
            key=namespaced_event_key,
            payload=payload_str,
            eventTimestamp=proto_timestamp_now(),
            additionalMetadata=meta_bytes,
        )

        return cast(Event, self.client.Push(request, metadata=get_metadata(self.token)))

    def _create_push_event_request(
        self,
        event: BulkPushEventWithMetadata,
        namespace: str,
    ) -> PushEventRequest:
        event_key = namespace + event.key
        payload = event.payload

        meta = event.additional_metadata

        try:
            meta_str = json.dumps(meta)
        except Exception as e:
            raise ValueError(f"Error encoding meta: {e}")

        try:
            payload = json.dumps(payload)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Error encoding payload: {e}")

        return PushEventRequest(
            key=event_key,
            payload=payload,
            eventTimestamp=proto_timestamp_now(),
            additionalMetadata=meta_str,
        )

    ## IMPORTANT: Keep this method's signature in sync with the wrapper in the OTel instrumentor
    @tenacity_retry
    def bulk_push(
        self,
        events: List[BulkPushEventWithMetadata],
        options: BulkPushEventOptions = BulkPushEventOptions(),
    ) -> List[Event]:
        namespace = options.namespace or self.namespace

        bulk_request = BulkPushEventRequest(
            events=[
                self._create_push_event_request(event, namespace) for event in events
            ]
        )

        response = self.client.BulkPush(bulk_request, metadata=get_metadata(self.token))

        return cast(
            list[Event],
            response.events,
        )

    def log(self, message: str, step_run_id: str) -> None:
        request = PutLogRequest(
            stepRunId=step_run_id,
            createdAt=proto_timestamp_now(),
            message=message,
        )

        self.client.PutLog(request, metadata=get_metadata(self.token))

    def stream(self, data: str | bytes, step_run_id: str) -> None:
        if isinstance(data, str):
            data_bytes = data.encode("utf-8")
        elif isinstance(data, bytes):
            data_bytes = data
        else:
            raise ValueError("Invalid data type. Expected str, bytes, or file.")

        request = PutStreamEventRequest(
            stepRunId=step_run_id,
            createdAt=proto_timestamp_now(),
            message=data_bytes,
        )

        self.client.PutStreamEvent(request, metadata=get_metadata(self.token))
