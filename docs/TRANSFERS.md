# Multipart transfer foundations

## Status and boundaries

Migration phase 2 is developing. `michiflin.parts` implements bounded local raw splitting, gap reporting and verified reconstruction. It is not yet a public URL downloader, queued transfer handler, mail sender, user interface, retention service or an end-to-end encrypted delivery claim. Existing lawful book delivery continues through its existing narrower path. No new executable card or member operation exposes filesystem paths or raw-part functions.

Callers must resolve and authorize source/destination paths inside the current identity's workspace, reserve disk for both staged and reconstructed data, and run the operation within an admitted bounded worker. The library does not accept chat addresses, invitations, arbitrary remote URLs or secrets. Its Linux no-clobber directory publication requires `renameat2(RENAME_NOREPLACE)` and fails closed when that facility/filesystem is unavailable.

## Raw format

A new private bundle directory contains `manifest.json` and sequential `part-00001.bin` files. Parts are exact raw slices, not ZIP/7z volumes. The manifest's format is `michiflin.raw-parts.v1` and includes a display filename, total byte count, whole-file SHA-256, configured part size and the ordered number/name/byte count/SHA-256 of each part.

Limits are 64 MiB/source, 1 MiB/part, 128 parts and a 40,000-byte manifest. Splitting keeps at most one part in memory; joining streams chunks up to 64 KiB. An empty source produces one explicitly empty, hashed part. Small part sizes that would exceed 128 parts are refused before creating a bundle. The manifest contains no server path, owner identity or source URL.

The parser rejects unknown/missing fields, duplicate JSON keys, unexpected versions, booleans used as integer sizes/numbers, bad hashes, wrong ordering/counts/lengths, repeated or path-like names, control/surrogate characters and oversized metadata. Directory inventory is streamed and rejects unlisted files without materializing an unbounded listing.

`inspect()` reports missing part numbers only; `verified=false` explicitly prevents confusing a complete-looking inventory with a successful checksum test. `join()` checks every part and the whole hash before publication. Its optional `expected_sha256` compares against a separately trusted checksum. SHA-256 proves byte consistency, **not authorship or authenticity**: an attacker who can replace both a manifest and its parts can replace their hashes too. Real transfers need authenticated encrypted transport and a trusted manifest/checksum.

## Filesystem safety and failure semantics

Every directory component is opened relative to an already opened descriptor with `O_NOFOLLOW`; dot-dot traversal is rejected. Input files must be regular, single-linked files opened with `O_NOFOLLOW|O_NONBLOCK`, rejecting symlinks, hard links, FIFOs and devices. Copying checks size/mtime/ctime/inode/device before and after, detects truncation/growth and preserves source files. It does not pretend to take a filesystem snapshot against a privileged adversary.

Split output is staged privately under an unpredictable name, flushed, then published as a complete directory using atomic no-replace rename. Even an existing empty directory or symlink is not overwritten. Join output is completely written and checked in a private temporary file, flushed and published with an atomic no-clobber hard link, then the temporary link is removed before syncing the parent. A result cannot be written inside its original bundle and make the bundle inventory inconsistent. Permissions are 0700 for the bundle and 0600 for files.

Before publication, validation/write failures clean only their own staging files and leave the source/destination intact. After publication, a failed directory sync reports `parts_publication_uncertain`: the complete result is retained and must be checked, not blindly retried or deleted. Existing destinations always require a distinct explicitly chosen path; there is no silent overwrite or automatic replacement. Failure/concurrent-publication tests verify these properties. Crash-time cleanup/retention of abandoned staging directories is still a supervisor integration task.

These are bounded local-file primitives, not an OS process sandbox or a file manager. Network filesystem latency, worker lifetime, aggregate concurrency/storage reservations and user isolation must be enforced by the calling queue/artifact layer. The present offline JSON sandbox deliberately mounts no host workspace, so it cannot simply be handed a filename and assumed to support disk-backed transfer work.

## Mail budgets and webxdc

`mail_part_budget()` reserves configurable overhead, accounts conservatively for base64 encoding with 76-character lines and CRLF, and caps raw content at 1 MiB/part. Defaults reserve 128 KiB; the configured allowance cannot be below 64 KiB. This is an arithmetic planning bound, not proof of a particular provider's maximum mail size, OpenPGP overhead, Nauta tariff or actual delivery. Provider-specific settings, measured encrypted messages, batches/delays/night windows, quotas and sending-state reconciliation remain pending.

An input `.xdc` is copied as raw bytes exactly like any other file and is never launched, unpacked or renamed into an executable part. Individual parts use `.bin`. Reconstruction preserves bytes and returns an integrity result; it does not establish that an input is a valid/safe miniapp or open it automatically. A real miniapp export additionally requires format validation and a native Delta Chat open test.

## Acceptance evidence and next gates

`tests/test_parts.py` covers exact raw/empty/boundary round trips, SHA-256 and trusted external hash checks, gaps, corrupt/truncated/oversized parts, whole-file mismatch, malformed/duplicate/traversal manifests, symlinks/hardlinks/FIFOs, private modes, unchanged originals, parent symlinks, source changes, disk failures, concurrent split/join publication, no-clobber destinations and post-publication sync uncertainty. English/Spanish errors preserve locale parity and existing browser regressions remain required.

Still required before phase-2 completion: reviewed disk-backed job handlers and safe URL downloads, identity-bound artifact inventory, mailbox/Nauta settings and scheduling, cancellable bounded network workers, durable per-part receipts and explicit selected-part resend, retention/cleanup, queue/transfer UI, authenticated backup inclusion for artifacts, and a real encrypted send/receive/reconstruction/hash test including restart and uncertain-send failures. Passing local splitting tests is not encrypted transfer delivery evidence.
