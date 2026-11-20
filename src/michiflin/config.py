from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path("state")
    public_access: bool = True
    daily_requests: int = 40
    max_pending: int = 3
    admin_keys: dict[str, str] = field(default_factory=dict, repr=False)
    online_providers: tuple[dict, ...] = field(default=(), repr=False)
    local_model_url: str = ""
    contact: str = ""

    @classmethod
    def from_environment(cls) -> Settings:
        raw = json.loads(os.getenv("MICHIFLIN_ADMIN_KEYS", "{}"))
        if not isinstance(raw, dict):
            raise ValueError("MICHIFLIN_ADMIN_KEYS must be an address-to-fingerprint mapping")
        admins = {}
        for address, fingerprint in raw.items():
            if not isinstance(address, str) or not isinstance(fingerprint, str):
                raise ValueError("Invalid administrator configuration")
            fingerprint = fingerprint.replace(" ", "").upper()
            if not re.fullmatch(r"[A-F0-9]{40,64}", fingerprint):
                raise ValueError("Administrator fingerprints must be pinned")
            admins[address.strip().casefold()] = fingerprint
        providers = json.loads(os.getenv("MICHIFLIN_PROVIDERS", "[]"))
        if not isinstance(providers, list) or any(not isinstance(item, dict) for item in providers):
            raise ValueError("MICHIFLIN_PROVIDERS must be a provider list")
        daily = int(os.getenv("MICHIFLIN_DAILY_REQUESTS", "40"))
        pending = int(os.getenv("MICHIFLIN_MAX_PENDING", "3"))
        if not 1 <= daily <= 10000 or not 1 <= pending <= 20:
            raise ValueError("Invalid resource limits")
        return cls(
            state_dir=Path(os.getenv("MICHIFLIN_STATE_DIR", "state")),
            public_access=os.getenv("MICHIFLIN_PUBLIC_ACCESS", "1") == "1",
            daily_requests=daily,
            max_pending=pending,
            admin_keys=admins,
            online_providers=tuple(providers),
            local_model_url=os.getenv("MICHIFLIN_LOCAL_MODEL_URL", ""),
            contact=os.getenv("MICHIFLIN_SERVICE_CONTACT", ""),
        )
