# Private operations and release gates

The public repository contains code and validation only. Runtime credentials, administrator pins, account exports, recovery keys and invitation URLs belong exclusively in private storage. No user needs a temporary access grant for ordinary public use.

## Account migration

1. Stop the previous runtime and confirm its scheduled workflow is disabled.
2. Authenticate the encrypted source snapshot before extracting anything.
3. Restore into a new private directory. Never overwrite or reset the original account.
4. Run a SQLite integrity check and compare the restored public-key fingerprint to the privately retained identity. Do not print the invitation URL.
5. Initialize a clean workspace database. Do not import executable legacy application code or previous instance permissions.
6. Pin administrators only after verifying their native cryptographic identities. An address that has not been paired and verified remains pending, even when it is on the owner's desired administrator list.
7. Create a new-format encrypted snapshot and independently restore it. Preserve legacy recovery assets separately.

This reconstruction's actual account identity and provider migration have been checked in isolated private storage without starting mail IO. That does not certify a running production deployment.

## Snapshot commands

Install the `bot` extra. Stop the runtime first. The snapshot command refuses an active runtime lock and never starts mail IO.

```sh
python -m michiflin.cli snapshot --accounts /private/runtime/accounts --state /private/runtime/state --output /private/next.snapshot --passphrase-file /private/backup.key
python -m michiflin.cli restore --input /private/next.snapshot --output /private/restoration-test --passphrase-file /private/backup.key
```

The passphrase file must have owner-only permissions, not be a symlink, and contain at least 24 characters. Snapshots combine a native encrypted account export, a consistent SQLite workspace copy, staged delivery files, and an authenticated inventory. The outer archive uses streaming AES-256-GCM with a fresh salt and nonce and a scrypt-derived key. Restoration checks authentication, archive paths, file digests, SQLite integrity, and the native account key before making the new destination visible. Existing destinations are never overwritten. Logs and provider configuration are not packed into this archive.

Queued deliveries can resume only after the restored owner, chat and instance bindings pass again. A delivery whose sending outcome was interrupted is uncertain and is never automatically sent again.

## Final launch gates

- Green source, browser, native-core, package, and restore checks on the exact revision.
- At least one verified online model and an explicitly identified, tested local model when local mode is offered.
- Reachability of the actual configured data sources from the intended runtime network. A public-source diagnostic may fail during upstream maintenance; that is not converted into a fabricated result or a silently substituted source.
- Real encrypted roundtrip using a separate client, plus a native miniapp interaction. Synthetic status updates and browser fixtures are not sufficient.
- Private scheduled deployment, exclusive account ownership, bounded uptime, authenticated backup at shutdown, restore verification, and a tested restart path.
- No invitation, administrator address, provider credential or account log in a public job or artifact.

Do not interpret a built `.xdc`, a restored account, or a green local test suite as approval to bypass the remaining gates.


## Optional live mail validation

The test creates two separate temporary accounts, never imports the production account, requires explicit opt-in, and stops mail IO when done. Keep its directory private. It does not grant any administrator role. Core protocol delivery is tested; it does not replace an actual miniapp interaction in a native client.

```sh
PYTHONPATH=src python tools/live_roundtrip.py --accept-create-test-accounts --directory /private/new-live-test
```

Use `--resume-test-accounts` only with the marked test directory to reuse those two accounts rather than creating more. Success verifies encrypted miniapp transport, a normal public-member handshake, the arithmetic request/response, and durable replay protection for note creation. Safe progress markers are printed; addresses, invitations and mailbox configuration are not printed.


## Bounded model execution and atomic notes

Incoming assistant requests run in a separate short-lived worker with a 95-second total deadline, including provider planning, read-only tools and final synthesis. Cancellation kills and reaps that worker; provider outcomes interrupted by the deadline are not automatically retried. The worker receives only its selected provider configuration, not administrator pins, backup tokens or other providers. Invalid provider schemas and nonstandard HTTPS endpoints are not advertised. Local endpoints must remain loopback HTTP chat-completion services.

Note additions and deletions commit their mutation and replayable response in one SQLite transaction. A failed checkpoint rolls back the mutation. After a successful commit, replay across runtime restarts returns the original result without repeating the edit.

## Private session supervisor

`python -m michiflin.deployment --validate-only --directory /private/session` exercises the actual encrypted seed or latest completed checkpoint, creates a fresh snapshot, uploads it to a **private writable** release repository, downloads it again, and independently restores it. This mode never starts mail IO. Its validation markers do not advance production state.

All deployment settings come from private environment configuration: `MICHIFLIN_DEPLOY_CONFIG`, `MICHIFLIN_BACKUP_PASSPHRASE`, `MICHIFLIN_BACKUP_REPOSITORY`, `MICHIFLIN_BACKUP_TOKEN`, `MICHIFLIN_SEED_ASSET_ID`, `MICHIFLIN_SEED_SHA256`, and the exact `MICHIFLIN_DEPLOY_REVISION`. Runtime configuration contains `providers`, pinned `admin_keys`, and a matching `backup_passphrase`. No configuration value belongs in a public workflow or artifact.

Production additionally requires `MICHIFLIN_LAUNCH_REVISION` to equal the exact reviewed source revision. The private hosting workflow must share the legacy runtime's concurrency group, leave the legacy workflow disabled, and default to validation only. Never enable its schedule or arm a launch until every final launch gate above is satisfied.

A live session publishes an immutable running marker **before** mail IO starts. Normal bounded shutdown first stops and waits for the runtime, then authenticates and independently verifies the uploaded checkpoint before publishing its completed marker. The next session selects only the latest session's completed checkpoint. If its completed marker is absent, automatic restart stops instead of silently restoring stale state. A failing live session retains its private local state, and any completed encrypted rescue snapshot remains under `recovery/`; the private hosting workflow must preserve only those encrypted rescue files as a restricted artifact on failure. An interrupted runner can still lose uncheckpointed state, so this design fails closed rather than promising lossless recovery from arbitrary infrastructure termination.

Snapshot history and original seeds are retained; this supervisor never deletes legacy recovery assets or guesses a backup to roll back to. Manual recovery of a failed session requires inspection of the retained encrypted rescue checkpoint. A native-client UI interaction, upstream book download and pending administrator pairing remain separate gates, not consequences of a successful supervisor validation.


### Verified native-client check

The current package was opened from an actual attached miniapp message in an isolated Delta Chat Desktop 2.69.1 profile using a synthetic `.invalid` account, never the production identity. The native webxdc window emitted a real handshake status update without an injected transport or backend response. Offline `17 * 23 = 391`, the Spanish locale switch, dark preferences, all thirteen utility cards and local password generation were exercised with no miniapp renderer error. The unconfirmed bot-connection state remained honest. This complements, but does not replace, the separate encrypted two-account mail roundtrip.

The supervisor's native regression additionally runs a synthetic runtime, stops it normally, acquires its released lock, snapshots its identity/workspace, restores them, and starts and stops the restored runtime. No production mail account is used by this regression.


## Local multimedia native validation

The expanded sixteen-card package has also been exercised in a fresh synthetic Delta Chat Desktop profile. Native tests opened the actual webxdc window, generated a WAV preview, detected an unsafe ZIP path, protected a CSV formula and invoked the real native export destination dialog. The destination picker was cancelled without selecting a recipient. No fake webxdc bridge, model response or production identity was used for that UI check. Export dialog success is not a file-delivery receipt. The checks supplement, rather than replace, encrypted transport and deployment validation.


## Cooperative native shutdown

The runtime owns its native event loop directly. SIGINT and SIGTERM set a stop flag instead of raising asynchronous exceptions inside RPC calls. A bounded 256-event queue applies backpressure, and events from any other account are ignored. The native core inherits the private supervisor process group so a forced group shutdown cannot intentionally leave a detached native core behind.

Shutdown waits for the selected account's `stop_io` acknowledgement, closes the request writer and bounds native-process exit. A native reader may still have an outstanding `get_next_event` request; that does not justify an unlimited wait. Failure to acknowledge IO shutdown is reported as unclean and the core is killed before restoration or snapshot access. The supervisor allows a bounded 180-second cleanup window covering an in-flight model request and native shutdown. A nonzero runtime exit still gets an authenticated recovery checkpoint, but does not produce a successful runtime-session job.
