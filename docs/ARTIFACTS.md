# Private server copies

## Implemented scope

`ArtifactStore` stores immutable server copies, not arbitrary filesystem paths. File IDs are random opaque identifiers. Every member operation authenticates the existing encrypted one-to-one identity and issued miniapp binding, then requires the exact owner storage key, chat and instance. A different key, chat or even another issued instance cannot access an existing instance's files. Cross-instance migration/sharing is not implicit; future migration must be explicit and identity-bound.

The compact **Server copies / Copias en servidor** tool implements actual upload, listing, metadata/checksum display, small-copy export and confirmed deletion. It is not a complete remote file manager: there are no folders, multiselect, moves, bulk rename, undo/redo, editing, archive extraction or multimedia processing controls. The new card uses the existing compact design and file-category filtering; earlier local file tools remain separate.

- `files.put` accepts a display name and strict base64 bytes, up to **12 KiB**. It uses the authenticated wire request ID, never an identifier supplied in its arguments. Extra fields, source paths and bad encoding are rejected.
- `files.list` returns at most 50 non-deleted records for this instance (live/uncertain copies first, failed history afterward); `files.status` queries an opaque ID. Neither reads file contents or attempts recovery.
- `files.read` exports only copies up to **12 KiB**, checks the stored file's size and SHA-256 before returning bytes, and refuses non-ready copies. The browser independently checks byte count and SHA-256 before creating an export. Request checkpoints retain metadata only, not base64 file bodies. Exact read replay reauthorizes/rechecks the current file and cannot bypass subsequent deletion by retrieving stale cached bytes.
- `files.delete` requires explicit user confirmation in the UI. It removes only the server copy. It does not erase the original phone file, original encrypted mail/status-update history, local downloads/drafts or earlier encrypted snapshots. Deleted metadata remains as a replay guard and does not recreate a file on repeated upload requests.

Opening the screen does not dispatch file operations. Listing is manual; no upload, deletion, automatic refresh, automatic network download or model call occurs on navigation. Offline controls and controls unsupported by an older bot are disabled. Filenames are rendered as text, never HTML. Read responses are silent/ephemeral in the UI transport, so the inline body is not added to local activity storage. Export creates a new fixed-name `.bin` copy; the original display name is shown separately. A `sendToChat` draft remains editable/cancellable and is not reported as receipt or delivery. Nothing opens or executes an uploaded `.xdc`.

## Explicit native attachment ingestion

Attach a fully downloaded file with the exact caption **`/save`** or **`/guardar`** in the encrypted private bot chat, then manually refresh **the most recently bot-issued miniapp**. The adapter selects the latest live issued instance bound to that identity/chat. The source filename/path comes from the native RPC message, never from command text or a status-update filesystem argument. An arbitrary chat attachment without that caption is not imported. Forwarded/info/self messages are ignored; unknown senders, plaintext/groups, blocked/unavailable identities, missing/undownloaded attachments and files over **16 MiB** are refused. An uploaded miniapp cannot register itself by being saved as a file.

Native save has the existing per-user/global daily and pending-request admission, plus the same storage quotas as miniapp uploads. Repeated processing of the same message/instance uses a durable request ID and does not copy it twice. Saving into a newly issued instance is a separate scope; old instances are not silently merged or rebound. No automatic chat acknowledgement or file send is performed: check the durable record by refreshing the file list. Immediate translated chat error notices and cross-instance transfer workflows remain to implement. Large-file export/multipart sending is also pending; importing a 16 MiB copy does not imply it can already be exported through the 12 KiB miniapp path.

The native test uses actual RPC-created encrypted message/blob metadata but **simulates the incoming event**, never starts mail IO, and does not claim a remote encrypted receive/production lifecycle or a phone UI test.

## Storage, concurrency and failures

Defaults are 16 MiB/file, 32 MiB and 16 retained files/person, and 64 MiB/64 retained files globally. Empty files still consume file/record quotas. Replay history is bounded at 256 records/person and 4096 globally; admission fails closed when that history is full instead of evicting duplicate guards. Administrator request exemptions do not exempt artifact storage. These are private application-level quotas, not filesystem reservations against unrelated processes filling the host disk.

A shared admission check preserves the existing backup bound: at most 100 potentially retained artifact/pending-delivery files, leaving four slots for account, database, manifest and optional catalogue within the strict 104-entry snapshot limit. Checks run in the same immediate SQLite transaction as file admission or delivery queueing. Missing uncertain files still count conservatively. No data is silently deleted to make space.

The private 0700 vault contains generated `<id>.blob` names, never user-chosen paths; files are 0600. Every directory component and input is opened relative to a verified directory descriptor with no symlink following. Inputs must be bounded, single-linked regular files: links, devices and FIFOs are refused. Copies are hashed, staged under an exclusive `<id>.partial`, flushed, atomically published with no-replace rename, directory-synced and checkpointed. Reading pins the immutable file in SQLite and rechecks its hash. Deletion cannot release storage while a reader is active. A trusted caller must keep the read context open until any consuming worker has actually been reaped.

`importing -> ready` and `ready -> deleting -> deleted` are the normal paths. Failed prepublication copies remove only their own temporary file, retain a failed request guard and release their storage reservation. Failure/interruption after publication is **uncertain** and retains bytes/reservations; this includes an interruption immediately after rename, before the Python publication flag changes. No automatic retry, resend or quota release assumes that a filesystem side effect did not happen.

Startup recovery occurs only after the existing exclusive runtime lock, alongside request/outbox/job recovery. Interrupted import/deletion and a ready file with an abandoned reader pin become uncertain. Constructors and opening screens do not perform recovery. Pins/reservations are not cleared just because a process disappeared from memory. Operator-only reconciliation requires verified supervisor/worker exit and an evidence SHA-256; it rechecks a ready file or explicitly deletes its server files, and appends evidence without overwriting earlier resolutions. This hook is not exposed as a member/admin operation.

Native copying is a bounded-byte, non-decoding local copy with between-read deadlines; those checks do not preempt a kernel filesystem operation stalled indefinitely. The first reviewed artifact-to-worker bridge now retains a read pin through isolated checksum execution and reaping (`JOBS.md`); it is not registered as a member operation or started by production. Additional asynchronous disk-backed handlers, hard parent-side copy/hash time bounds, transfer scheduling and output publication remain to implement. The offline sandbox is never given the entire vault or host filesystem.

## Authenticated snapshots

The existing AES-GCM snapshot includes the consistent SQLite metadata plus an optional bounded artifact inventory. Ready blobs are copied into a private snapshot staging directory and checked against database byte counts and SHA-256 before encryption. Published uncertain blobs can be preserved as uncertain; missing uncertain blobs do not become ready. Unpublished `.partial` files are not promoted or claimed as recovered data. A snapshot refuses in-progress import/deletion and mismatched/missing ready files. Stop/quiesce the runtime under its lock before creating one.

Restore preserves the 104-entry and 512 MiB aggregate bounds, imposes 16 MiB/artifact and a 64 KiB manifest bound, and permits only the exact generated artifact filename pattern. The manifest inventory, authenticated SQLite inventory, sizes, checksums, archive membership and file types must match **before native identity import**. Arbitrary entries, traversal, duplicate entries, symlinks and malformed/extra metadata fail closed. Native identity verification still occurs before final publication. Files restore with private permissions, and uncertainty/pins survive conservatively until startup recovery/reconciliation. Older snapshots without the artifact table/inventory remain supported.

Deletion does not retroactively alter old immutable encrypted backups. Backup retention and user-visible recovery/export settings remain separate work. These are new-workspace snapshots, not a claim to have imported unavailable legacy/phone data or restored old administrator permissions.

## Evidence

- `test_artifacts.py`: isolation, exact/concurrent replay, quotas, safe sources, immutable originals, hash checks, reader pins, no-clobber publication, interrupted publication, deletion and repeated operator evidence.
- `test_service.py`: real protocol upload/read/delete, authorization/session/quota gates, opaque IDs, request-ID/path injection rejection, and no persistent read bodies or deletion bypass through cached responses.
- `test_adapter.py`: explicit native caption, sender/encryption/binding/download guards, no self-binding/automatic reply and duplicate prevention.
- `test_native_core.py`: actual native blob path and metadata with a simulated incoming event, original preservation and no mail IO.
- `test_backup.py`: native authenticated account/database/file roundtrip, older snapshot compatibility, uncertain missing file preservation and malformed artifact rejection before native import.
- `test_artifact_browser.py`: real service upload/list/hash/export/delete in EN/ES Chromium/WebKit, mobile/desktop layout, text-only hostile filename, no screen-triggered operations, offline/older-bot gating and oversized-input refusal.

End-to-end encrypted multipart sending/reconstruction, public downloads, full file-manager parity, disk-backed fair jobs and controlled production startup remain separate acceptance gates.
