# Durable jobs and offline workers

## Delivery status

This is the first implementation increment of migration phase 1, not completion of the transfer service or a production launch. Existing small synchronous operations and encrypted book delivery still use their existing request/outbox paths. There is no general job submission API, no new executable miniapp card and no automatic worker thread in production. Reviewed, versioned job kinds must be registered explicitly before admission; an empty registry refuses submission.

`Service.jobs` owns the queue. Authenticated `jobs.list`, `jobs.status` and `jobs.control` operations retain the existing encrypted one-to-one identity, instance, session and request replay gates. Responses omit owner identifiers, source payloads, checkpoints and worker lease tokens. They expose actual worker-stop confirmation independently of a completed/cancelled state. Listing returns at most the latest 50 jobs for that exact instance; older known IDs remain individually queryable.

## Admission and fairness

- SQLite `BEGIN IMMEDIATE` serializes submissions, claims and transitions across threads/processes. Tables are additive and reopening the old database is supported.
- A submission is unique by instance and request ID. Its kind, canonical payload, storage reservation, delay and lifetime form a digest. Exact replay returns the same job, including terminal/uncertain jobs. Changed input is a conflict, never a second execution. Finished replay guards are not silently evicted.
- Each owner receives a round-robin turn, then FIFO within that owner. New owners begin at the current turn. One owner's active/uncertain process cannot consume another owner's configured slots.
- Defaults: 3 pending jobs/person, 64 globally, 1 active worker/person, 2 globally; 64 MiB reserved/person, 256 MiB globally. Pending includes paused and uncertain work. All attempts, including terminal retained results, count toward storage reservations until trusted cleanup confirms files are removed.
- Histories stop admission at 256 records/person or 4096 globally. This intentionally fails closed instead of silently dropping replay protection. Archival/tombstone compaction and user-visible retention settings remain to implement.
- Delay must be shorter than expiry; lifetime is at most 24 hours. A reviewed kind sets at most 900 seconds of cumulative execution time. Offline sandbox attempts have a stricter maximum 60-second wall/30-second CPU policy.

## States, fencing and restart

`queued -> running -> completed | failed` is the normal path. Queued jobs may pause or cancel without starting. A running job first becomes `pausing` or `cancelling`; it is not reported as stopped until the supervising parent has actually reaped its process. A completion racing with cancellation remains a real completion rather than a false promise that the operation did not happen.

A random worker token is persisted only as a SHA-256 digest and fences heartbeats/results. Progress is monotonic, checkpoints are bounded, and heartbeats cannot extend the original attempt deadline. Pausing subtracts time already spent from the original kind budget; resume does not grant fresh execution time. A missing versioned handler cannot claim/resume persisted work.

Expired queued/paused jobs become expired. Missed leases/deadlines and interrupted running/pausing/cancelling jobs become **uncertain**, not queued. They retain their process slot: an expired lease is not proof that an old process has stopped. `Ledger.recover_interrupted()` performs this transition alongside existing request/outbox recovery after the runtime lock is acquired. Opening a queue/service alone does not run restart recovery or release slots.

A trusted parent calls `worker_stopped(claim)` only after reaping. If it has no final confirmed outcome, stopping leaves the job uncertain. After a full restart loses in-memory tokens, `reconcile()` is an operator-only Python hook: the operator must verify the old supervisor and complete worker namespace have exited, verify the actual external outcome, and supply a SHA-256 digest identifying the private evidence. The digest and outcome are recorded atomically. It never requeues and is not exposed as a member/admin operation. Storage remains reserved until separate cleanup verification. A cryptographic digest records evidence identity; it does not itself prove process death or successful external delivery.

## Real OS isolation

`sandbox.run()` requires Linux, Bubblewrap, util-linux `prlimit` and libseccomp. The startup probe actually runs a harmless isolated Python process. Missing/denied isolation fails closed; there is no direct Python/shell fallback. The host OS and its root-owned runtime installation are trusted and must not store private application credentials in `/usr` or the interpreter installation.

Each worker has new user, PID, network, IPC, UTS and mount namespaces, no capabilities, an empty environment except explicit nonsecret runtime variables, read-only system/interpreter mounts, no host `/home`, `/root`, `/etc` or `/proc`, and a new size-limited temporary filesystem. Root and device mounts are read-only. No account state, state database, provider keys, source proxy, host working directory or other job's temporary files are mounted. Python runs with isolated mode, without site packages or bytecode writes.

A libseccomp filter rejects sockets, process/thread creation, namespace changes, mounting, tracing, cross-process memory, io_uring setup and other kernel-control operations. `prlimit` enforces CPU, virtual memory, open-file, file-size and core-dump limits. The parent independently enforces wall time, cancellation and bounded JSON output; it kills/reaps the worker on all exit paths, including callback failures. Defaults are 5 s wall, 3 s CPU, 128 MiB virtual memory, 8 MiB temporary storage and 64,000 output bytes. Output spooling is additionally file-size bounded; it can briefly exceed the lower logical output cap before the parent detects it. This is a local Linux security boundary, not a claim of protection against kernel vulnerabilities or hostile system administrators.

`OfflineRunner` connects queue claims to reviewed scripts, periodically renews leases, observes controls, and publishes only bounded JSON results. There are no external side effects or delivery actions in these handlers. Pause kills this isolated attempt and discards its temporary files; resume recomputes within the remaining cumulative budget, not from a fictional suspended process. Hosting shutdown leaves the affected job uncertain, not implicitly user-cancelled. The supervisor must own runner threads and the runtime lock until shutdown. Neither `Service` initialization nor opening a screen starts the runner.

The existing authenticated backup already includes a consistent SQLite copy, so job rows, results and checkpoints travel with it. Native offline snapshot tests verify restored completed/queued/interrupted jobs and conservative startup recovery. This does not include arbitrary future job files: those need an explicit bounded artifact inventory and restore policy.

Network-enabled downloads/media, external cancellation, disk-backed artifacts and their encrypted backup inclusion, fair bandwidth/CPU shares, resumable provider checkpoints, transfer retention, a queue UI and native-client execution remain separate gates. Do not reuse this offline-only sandbox as an extractor proxy or expose arbitrary handler/script selection through job arguments.

On Ubuntu with restricted unprivileged user namespaces, installing Bubblewrap alone may not grant namespace creation. After installing distribution `bubblewrap` and `libseccomp2`, the explicit root-only `tools/setup_sandbox.sh` can install a narrowly attached AppArmor profile for the root-owned `/usr/bin/bwrap`. It never disables the global restriction and refuses to overwrite an existing profile. This is an operator/CI host setup action, never something the service runs. Run `python tools/check_sandbox.py` afterward; a successful package installation or namespace-only probe is not a complete sandbox check.

## Evidence

- `tests/test_jobs.py`: persistence, exact replay/conflicts, parallel admission/claim, per-owner/global limits, FIFO/fairness, authorization/revocation, progress/checkpoints, pause/resume budget, cancellation/completion races, expiry, stale leases, startup uncertainty, token fencing, reconciliation and verified cleanup.
- `tests/test_sandbox.py`: real namespace execution, private-path/environment exclusion, networking/fork/namespace denial, read-only mounts, disk/CPU/memory/wall/output limits, cancellation, output validation and fail-closed unavailable isolation.
- `tests/test_job_runner.py`: real queue-to-worker-to-result, failed computation, pause/cancel reaping, hosting shutdown uncertainty, registry mismatch and unavailable startup probe.
- `tests/test_service.py`: queue protocol operations retain encryption/session/identity/instance boundaries and do not expose submission/reconciliation.

Sandbox-dependent tests skip explicitly on unsupported platforms, but the dedicated Linux sandbox CI job sets `MICHIFLIN_REQUIRE_SANDBOX=1`, making an unavailable sandbox a failure rather than counted execution evidence. Existing bilingual/mobile/desktop/browser tests remain separate from queue/native-client acceptance.
