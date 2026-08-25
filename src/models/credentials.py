from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from src.models.errors import ModelErrorCode, normalize_model_error_code


SENSITIVE_CREDENTIAL_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "cookie",
        "set-cookie",
        "client_secret",
        "access_key",
        "refresh_token",
    }
)

ALLOWED_CREDENTIAL_REFERENCE_KEYS = frozenset(
    {
        "api_key_env",
        "credential_ref",
        "last_error_code",
        "last_error_at",
    }
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def contains_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in ALLOWED_CREDENTIAL_REFERENCE_KEYS:
        return False
    return normalized in SENSITIVE_CREDENTIAL_KEYS


def find_plain_secret_keys(value: Any, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}" if path else key_text
            if contains_sensitive_key(key_text):
                found.append(item_path)
            found.extend(find_plain_secret_keys(item, path=item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]" if path else f"[{index}]"
            found.extend(find_plain_secret_keys(item, path=item_path))
    return found


@dataclass(frozen=True)
class CredentialRecord:
    provider_conf_id: str
    credential_slug: str
    encrypted_secret: str
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["encrypted_secret"] = "***"
        return payload


@dataclass(frozen=True)
class CredentialResolution:
    success: bool
    slug: str
    source: str | None = None
    secret: str | None = field(default=None, repr=False)
    code: str | None = None
    error: str | None = None
    missing_config: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code is not None:
            object.__setattr__(self, "code", normalize_model_error_code(self.code))
        object.__setattr__(self, "missing_config", [str(item) for item in self.missing_config])
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self, *, include_secret: bool = False) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["secret"] = self.secret if include_secret else mask_secret(self.secret)
        return payload


CredentialLookup = Callable[[str], str | None]


def resolve_credential_secret(
    credential: Any,
    *,
    environ: Mapping[str, str] | None = None,
    credential_lookup: CredentialLookup | None = None,
) -> CredentialResolution:
    env = environ if environ is not None else os.environ
    slug = str(getattr(credential, "slug", "") or "default")
    enabled = bool(getattr(credential, "enabled", True))
    api_key_env = getattr(credential, "api_key_env", None)
    credential_ref = getattr(credential, "credential_ref", None)

    if not enabled:
        return CredentialResolution(
            success=False,
            slug=slug,
            code=ModelErrorCode.MISSING_MODEL_CONFIG,
            error=f"credential '{slug}' is disabled",
            missing_config=["enabled_credential"],
        )

    if api_key_env:
        secret = str(env.get(str(api_key_env), "")).strip()
        if secret:
            return CredentialResolution(
                success=True,
                slug=slug,
                source=f"env:{api_key_env}",
                secret=secret,
            )

    if credential_ref:
        if credential_lookup is None:
            return CredentialResolution(
                success=False,
                slug=slug,
                source=f"credential_ref:{credential_ref}",
                code=ModelErrorCode.MISSING_API_KEY,
                error="credential_ref cannot be resolved by the current credential store",
                missing_config=["credential_ref"],
            )
        secret = str(credential_lookup(str(credential_ref)) or "").strip()
        if secret:
            return CredentialResolution(
                success=True,
                slug=slug,
                source=f"credential_ref:{credential_ref}",
                secret=secret,
            )

    missing_config = []
    if api_key_env:
        missing_config.append(str(api_key_env))
    if credential_ref:
        missing_config.append(str(credential_ref))
    if not missing_config:
        missing_config = ["api_key_env", "credential_ref"]

    return CredentialResolution(
        success=False,
        slug=slug,
        code=ModelErrorCode.MISSING_API_KEY,
        error=f"credential '{slug}' has no available secret",
        missing_config=missing_config,
    )
