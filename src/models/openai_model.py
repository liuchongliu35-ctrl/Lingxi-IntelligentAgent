from __future__ import annotations

from typing import Any

from src.models.config import ProviderConf
from src.models.credentials import CredentialResolution, resolve_credential_secret
from src.models.providers.openai_compatible import (
    OpenAICompatibleModel,
    OpenAICompatibleProvider,
    configured_builtin_provider_conf,
)


class OpenAIModel(OpenAICompatibleModel):
    """Compatibility wrapper for the configured OpenAI-compatible adapter."""

    def __init__(
        self,
        *,
        provider_conf: ProviderConf | None = None,
        credential: CredentialResolution | None = None,
        client_factory: Any | None = None,
        client: Any | None = None,
    ):
        conf = provider_conf or configured_builtin_provider_conf("openai")
        resolved_credential = credential or resolve_credential_secret(conf.credentials[0])
        super().__init__(
            OpenAICompatibleProvider(
                conf,
                resolved_credential,
                client_factory=client_factory,
                client=client,
            )
        )
