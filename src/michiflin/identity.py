from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from .config import Settings
from .errors import AppError


@dataclass(frozen=True)
class Principal:
    address: str = field(repr=False)
    fingerprint: str = field(repr=False)
    chat_id: int
    encrypted: bool
    one_to_one: bool
    verified: bool = False

    @property
    def storage_key(self) -> str:
        value = self.address.strip().casefold() + "\0" + self.fingerprint.replace(" ", "").upper()
        return hashlib.sha256(value.encode()).hexdigest()

    def is_admin(self, settings: Settings) -> bool:
        pinned = settings.admin_keys.get(self.address.strip().casefold(), "")
        actual = self.fingerprint.replace(" ", "").upper()
        return bool(pinned and self.verified and self.encrypted and self.one_to_one
                    and hmac.compare_digest(pinned, actual))

    def require_access(self, settings: Settings) -> None:
        if not self.encrypted or not self.one_to_one or not self.fingerprint:
            raise AppError("encrypted_private_chat_required")
        if not settings.public_access and not self.is_admin(settings):
            raise AppError("service_unavailable")

    def require_admin(self, settings: Settings) -> None:
        self.require_access(settings)
        if not self.is_admin(settings):
            raise AppError("administrator_required")
