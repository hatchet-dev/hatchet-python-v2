import json
import os
from logging import Logger, getLogger

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hatchet_sdk.token import get_addresses_from_jwt, get_tenant_id_from_jwt


class ClientTLSConfig(BaseModel):
    tls_strategy: str
    cert_file: str | None
    key_file: str | None
    ca_file: str | None
    server_name: str


def _load_tls_config(host_port: str | None = None) -> ClientTLSConfig:
    server_name = os.getenv("HATCHET_CLIENT_TLS_SERVER_NAME")

    if not server_name and host_port:
        server_name = host_port.split(":")[0]

    if not server_name:
        server_name = "localhost"

    return ClientTLSConfig(
        tls_strategy=os.getenv("HATCHET_CLIENT_TLS_STRATEGY", "tls"),
        cert_file=os.getenv("HATCHET_CLIENT_TLS_CERT_FILE"),
        key_file=os.getenv("HATCHET_CLIENT_TLS_KEY_FILE"),
        ca_file=os.getenv("HATCHET_CLIENT_TLS_ROOT_CA_FILE"),
        server_name=server_name,
    )


def parse_listener_timeout(timeout: str | None) -> int | None:
    if timeout is None:
        return None

    strategy: str = "tls"
    cert_file: str | None = None
    key_file: str | None = None
    root_ca_file: str | None = None
    server_name: str = ""


class HealthcheckConfig(BaseSettings):
    model_config = create_settings_config(
        env_prefix="HATCHET_CLIENT_WORKER_HEALTHCHECK_",
    )

    port: int = 8001
    enabled: bool = False


DEFAULT_HOST_PORT = "localhost:7070"


class ClientConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_default=True)

    token: str = os.getenv("HATCHET_CLIENT_TOKEN", "")
    logger: Logger = getLogger()
    tenant_id: str = os.getenv("HATCHET_CLIENT_TENANT_ID", "")

    ## IMPORTANT: Order matters here. The validators run in the order that the
    ## fields are defined in the model. So, we need to make sure that the
    ## host_port is set before we try to load the tls_config and server_url
    host_port: str = os.getenv("HATCHET_CLIENT_HOST_PORT", DEFAULT_HOST_PORT)
    tls_config: ClientTLSConfig = _load_tls_config()

    server_url: str = "https://app.dev.hatchet-tools.com"
    namespace: str = ""

    tls_config: ClientTLSConfig = Field(default_factory=lambda: ClientTLSConfig())
    healthcheck: HealthcheckConfig = Field(default_factory=lambda: HealthcheckConfig())

    listener_v2_timeout: int | None = None
    grpc_max_recv_message_length: int = Field(
        default=4 * 1024 * 1024, description="4MB default"
    )
    grpc_max_recv_message_length: int = int(
        os.getenv("HATCHET_CLIENT_GRPC_MAX_RECV_MESSAGE_LENGTH", 4 * 1024 * 1024)
    )  # 4MB
    grpc_max_send_message_length: int = int(
        os.getenv("HATCHET_CLIENT_GRPC_MAX_SEND_MESSAGE_LENGTH", 4 * 1024 * 1024)
    )  # 4MB
    otel_exporter_oltp_endpoint: str | None = os.getenv(
        "HATCHET_CLIENT_OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_service_name: str | None = os.getenv("HATCHET_CLIENT_OTEL_SERVICE_NAME")
    otel_exporter_oltp_headers: str | None = os.getenv(
        "HATCHET_CLIENT_OTEL_EXPORTER_OTLP_HEADERS"
    )
    otel_exporter_oltp_protocol: str | None = os.getenv(
        "HATCHET_CLIENT_OTEL_EXPORTER_OTLP_PROTOCOL"
    )
    worker_healthcheck_port: int = int(
        os.getenv("HATCHET_CLIENT_WORKER_HEALTHCHECK_PORT", 8001)
    )
    worker_healthcheck_enabled: bool = (
        os.getenv("HATCHET_CLIENT_WORKER_HEALTHCHECK_ENABLED", "False") == "True"
    )

    @field_validator("token", mode="after")
    @classmethod
    def validate_token(cls, token: str) -> str:
        if not token:
            raise ValueError("Token must be set")

        return token

        return self

    @model_validator(mode="after")
    def validate_addresses(self) -> "ClientConfig":
        if self.host_port == DEFAULT_HOST_PORT:
            server_url, grpc_broadcast_address = get_addresses_from_jwt(self.token)
            self.host_port = grpc_broadcast_address
            self.server_url = server_url
        else:
            self.server_url = self.host_port

        if not self.tls_config.server_name:
            self.tls_config.server_name = self.host_port.split(":")[0]

        if not self.tls_config.server_name:
            self.tls_config.server_name = "localhost"

        return self

    @field_validator("listener_v2_timeout")
    @classmethod
    def validate_listener_timeout(cls, value: int | None | str) -> int | None:
        if value is None:
            return None

        if isinstance(value, int):
            return value

        return int(value)

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, namespace: str) -> str:
        if not namespace:
            return ""

        if not namespace.endswith("_"):
            namespace = f"{namespace}_"

        return namespace.lower()

    @field_validator("tenant_id", mode="after")
    @classmethod
    def validate_tenant_id(cls, tenant_id: str, info: ValidationInfo) -> str:
        token = cast(str | None, info.data.get("token"))

        if not tenant_id:
            if not token:
                raise ValueError("Either the token or tenant_id must be set")

            return get_tenant_id_from_jwt(token)

        return tenant_id

    @field_validator("host_port", mode="after")
    @classmethod
    def validate_host_port(cls, host_port: str, info: ValidationInfo) -> str:
        if host_port and host_port != DEFAULT_HOST_PORT:
            return host_port

        token = cast(str, info.data.get("token"))

        if not token:
            raise ValueError("Token must be set")

        _, grpc_broadcast_address = get_addresses_from_jwt(token)

        return grpc_broadcast_address

    @field_validator("server_url", mode="after")
    @classmethod
    def validate_server_url(cls, server_url: str, info: ValidationInfo) -> str:
        ## IMPORTANT: Order matters here. The validators run in the order that the
        ## fields are defined in the model. So, we need to make sure that the
        ## host_port is set before we try to load the server_url
        host_port = cast(str, info.data.get("host_port"))

        if host_port and host_port != DEFAULT_HOST_PORT:
            return host_port

        token = cast(str, info.data.get("token"))

        if not token:
            raise ValueError("Token must be set")

        _server_url, _ = get_addresses_from_jwt(token)

        return _server_url

    @field_validator("tls_config", mode="after")
    @classmethod
    def validate_tls_config(
        cls, tls_config: ClientTLSConfig, info: ValidationInfo
    ) -> ClientTLSConfig:
        ## IMPORTANT: Order matters here. This validator runs in the order
        ## that the fields are defined in the model. So, we need to make sure
        ## that the host_port is set before we try to load the tls_config
        host_port = cast(str, info.data.get("host_port"))

        return _load_tls_config(host_port)

    def __hash__(self) -> int:
        return hash(json.dumps(self.model_dump(), default=str))
