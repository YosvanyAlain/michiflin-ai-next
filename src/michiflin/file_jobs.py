from __future__ import annotations

from .artifacts import identifier
from .errors import AppError
from .job_runner import OfflineHandler
from .jobs import Kind


def _checksum_input(payload: dict) -> None:
    if set(payload) != {"artifact"}:
        raise AppError("invalid_checksum_job")
    identifier(payload["artifact"])


CHECKSUM_KIND = "files.checksum.v1"
CHECKSUM_HANDLER = OfflineHandler(
    kind=Kind(_checksum_input, seconds=30),
    artifact_field="artifact",
    script='''import hashlib,json
size=0
checksum=hashlib.sha256()
with open('/input.bin','rb') as source:
    for block in iter(lambda:source.read(65536),b''):
        size+=len(block)
        checksum.update(block)
print(json.dumps({'bytes':size,'sha256':checksum.hexdigest()}))
''',
)
