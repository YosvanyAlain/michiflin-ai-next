# Approved implementation tracking

The compact bilingual Michiflin AI design is retained. This matrix tracks functional migration, not the number of cards or passing tests. Source, comments and commits stay English; UI stays English/Spanish. Private identities, invitations, state and credentials are excluded from public evidence.

Status vocabulary: **pending**, **developing**, **implemented** (code exists), **verified** (the specified acceptance was demonstrated), **blocked** (an external action is necessary). A component's local verification does not promote its whole phase to verified. Earlier implemented utilities remain subject to regression tests and the existing release gates.

| Phase / capability | Status | Dependencies | Acceptance test / evidence | Remaining limitations |
| --- | --- | --- | --- | --- |
| 1a. Durable admission, fairness, controls and restart fencing | Verified locally and in CI for this component | Existing identity-bound Ledger | `test_jobs.py`, `test_service.py`, native snapshot regression; CI `44827368548` at `bbc5555`; see `JOBS.md` | No general submission route or queue screen; history archival pending |
| 1b. Offline bounded processes and queue runner | Verified on local Linux and dedicated Ubuntu host CI | Bubblewrap, libseccomp, prlimit; exclusive supervisor | `test_sandbox.py`, `test_job_runner.py`; mandatory isolation job in CI `44827368548` | Not started by production; no network/artifact handlers, resource-share scheduler or native queue client yet |
| 1c. Complete job integration, progress UI and resource policy | Developing | 1a/1b; each reviewed module | Encrypted native requests, real output, shutdown/restart and UI controls | Phase 1 is not complete |
| 2. Safe public transfers, splits, mailbox/Nauta budgets, windows and retention | Developing; raw-part library and bounded artifact inventory implemented | 1; public-address-pinned transport; durable outbox | Raw/hash/no-clobber tests and authenticated artifact restore; see `TRANSFERS.md` and `ARTIFACTS.md`. Final gate: encrypted multipart receive/reconstruction/SHA-256 and uncertain-send recovery | Inline copies are limited to 12 KiB. No arbitrary public download endpoint, disk-backed fair worker, multipart mail sender, Nauta scheduling or retention yet |
| 3. Local copies and isolated remote file manager | Developing; immutable private copies/list/hash/export/delete implemented | 1/2; identity-bound artifact store | `test_artifacts.py`, real-service EN/ES Chromium/WebKit copy workflow, native blob with simulated incoming event, authenticated restore; see `ARTIFACTS.md` | Native import up to 16 MiB, inline upload/export up to 12 KiB. Folders/multiselect/move/rename/undo/edit, cross-instance migration and real encrypted phone/server/chat lifecycle remain pending |
| 4. Archives, volumes and joining | Pending | 1/3; isolated format handlers | ZIP/7z passwords/volumes; selective RAR; gaps/hash/media compatibility; adversarial extraction | No traversal/symlink/duplicate/bomb/partial publication; sensitive payloads encrypted |
| 5. Multimedia and YouTube | Pending | 1/2/4; isolated auxiliary network gateway | Actual permitted playable download/transcode/segments/transcript and encrypted delivery | Current public oEmbed metadata is not downloading/player/transcript support |
| 6. Search and complete reader | Pending beyond existing text reader | 1/2; attributed providers | Live search/news/video/reference results, reader actions, saved links/PDF/screenshots/downloads | Existing pinned text reader is real but narrower |
| 7. Connected applications | Pending | 2/6; authorized external APIs | Non-model translation; labeled fresh/history elTOQUE; topical news; licensed Archive albums/tracks | Availability, provenance, permitted calculations and rights must be checked per provider |
| 8. AI tools, private providers, memory and local model | Pending beyond current gateway | 1/6/7; explicit consent/cost routing | Cited extractive path, bounded tools, quota/health, per-user providers, attachments, deletion and real provisioned local inference | No silent external fallback or free/admin-key model access |
| 9. Image studio | Pending | 1/2/8; provider provisioning | Genuine generation, quotas, gallery/regenerate/describe/export; uncertain-charge recovery | Never claim Arena internal models are available to the bot |
| 10. Programmer | Pending | 1b/3/4/8; project-specific isolation | Multi-file edits/diffs/undo/tests/fixes/preview, valid ZIP/xdc opened natively | Offline sandbox primitive is not a completed coding workspace |
| 11. Live browser | Pending | 1/2/3; isolated browser and compatible transport | Real-client frames/gestures/sessions/downloads, encrypted per-user state and limits | Mail/transport/data-plan limitations explicit; no fake live frame controls |
| 12. Admin, settings and UX | Pending beyond existing foundations | All module policies; pinned cryptographic admin identity | Diagnostics/limits/revoke/block/emergency/maintenance; per-user budgets/notices/commands/onboarding/sync; EN/ES and offline tests | Never mutate services merely by opening a screen |
| 13. Recoverable data | Developing inventory; module import pending | Per-module schemas; authenticated backups | Original-preserving dry run, counts/hashes, identity-bound mappings and restoration tests | Never restore old permissions or claim missing phone/temp data recovered |

## Data inventory started before module imports

| Data class | Current boundary | Import / restoration gate |
| --- | --- | --- |
| Existing native identity and encrypted checkpoints | Private authenticated deployment/backup path | Preserve original; verify before native import; do not publish identifiers |
| Existing online-provider configuration | Private runtime configuration | Inventory provider compatibility without printing keys; bind to current authorization |
| Optional Gutenberg catalogue | Bounded authenticated snapshot/bootstrap already implemented | Retain original fetched age and hash; fresh rights metadata remains required |
| Existing notes, request/outbox Ledger and new job rows | Private runtime SQLite included through the existing consistent SQLite backup and authenticated manifest | Native offline snapshot tests preserve completed results, queued work and checkpoints; interrupted jobs become uncertain and retain slots after startup recovery. |
| New immutable server copies | Optional authenticated artifact inventory; strict 104-entry snapshot bound retained | Actual native offline account/database/blob restore verifies sizes/hashes before identity import. Uncertain/pinned state is preserved; partial staging is not promoted. Arbitrary future worker files outside this inventory remain excluded. |
| Legacy per-module workspaces, browser state, images and memories | Availability not established by this increment | Inspect metadata privately, dry-run per module and keep originals; never import permissions |
| Phone-only/local miniapp state and temporary files | Not assumed available on server | Explicit user-controlled export/import; report unavailable data honestly |

## Cross-cutting completion gates

Each actual module delivery requires unit/integration/failure/security/privacy tests, real source/provider/output evidence where applicable, English/Spanish and mobile/desktop Chromium/WebKit/offline behavior, secret scanning and documented limits. Native Delta Chat testing is mandatory for transport/export behavior. The existing complete suite remains regression evidence, not feature parity.

Production stays stopped until implementation/testing and the controlled lifecycle are complete. Administrator pairing, historical GitHub queue investigation and authenticated backups proceed independently of local module development. Remaining external pairing/lifecycle issues are not reasons to stop work on independent modules. Final startup/use/delivery/shutdown/backup/restart must be tested rather than inferred from a green CI run.
