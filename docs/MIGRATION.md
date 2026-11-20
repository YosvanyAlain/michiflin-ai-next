# Capability migration

The previous application is a functional reference, not a source tree to import. This rebuild keeps a clean history, current security boundaries and the Michiflin AI identity. Private account state, provider keys, administrator identities and invitations stay outside this repository.

## What should survive

- Useful internet access over encrypted mail, especially on slow or expensive connections.
- A useful non-AI path: a person should not need a model to fetch a book, read a page or convert a file.
- Small, purposeful cards; a short route from intent to input to an actual result.
- Visible offline/connected boundaries, truthful pending/failure states and user-controlled exports.
- Bounded transfers, cancellation, per-person isolation and durable receipts that do not confuse submission with delivery.

The compact interface keeps the new typography, palette, illustration, themes and navigation. Cards show a name and an offline/bot label, with descriptions available as tooltips. Search and category chips replace a long descriptive catalogue. Meaningful privacy and export limitations remain available in expandable details rather than being deleted.

## Inventory and order

| Reference capability | Rebuild state | Next acceptance gate |
| --- | --- | --- |
| Arithmetic, units, clocks, notes and weather | Implemented; local and connected routes are distinct | Preserve bilingual and offline regressions |
| Book discovery and confirmed lawful delivery | Implemented; upstream availability remains a separate live gate | Real source metadata, validated ebook bytes and production delivery lifecycle |
| Local file/image/table/archive/WAV tools | Implemented; not a remote filesystem or universal transcoder | Keep file inputs private and native export user-controlled |
| WARP egress | Optional, scoped source-client adapter and manual Actions probe implemented | Verify WARP and each destination on the actual hosting runner |
| YouTube title/channel preview | Implemented via public oEmbed, explicitly without download or playback | Live source-route acceptance is separate from a successful WARP tunnel |
| Full media inspection and selected download | Not implemented | Isolated extractor, public-address validation, rights/terms policy, format/size/duration limits, bounded retries, cancellation and genuine playable output |
| Low-bandwidth page reader | Implemented with a separate DNS-pinned HTTPS transport, local pagination and explicit text export | Live host-route probes and production lifecycle; no JavaScript rendering or login bypass |
| Web search | Not implemented | Search-provider attribution, result limits and explicit follow-up through the safe reader |
| Transcripts, translation and Internet Archive music | Not implemented | Provider/track provenance, licensing, language selection, explicit failure and transfer budgets |
| PDF/OCR, screenshots and broader conversion | Not implemented | Resource-isolated workers, format validation and actual output tests |
| Coding workspace and miniapp export | Not implemented | No host shell or secrets, isolated execution, package validation and explicit export |
| Remote files, selective extraction and rename undo | Not implemented | Identity-bound storage, transactions, traversal/bomb/symlink defenses and restore tests |
| Split transfer/checksums and fair worker queue | Not implemented as a general transfer service | Mailbox-aware part budgets, safe joining, cancellation and crash/replay tests |

Isolated media extraction/download and web search are the next connected priorities. The safe text reader is now a real connected operation, not a placeholder. They are not replaced by cosmetic cards. No unavailable service is added to the executable catalogue merely to resemble the previous miniapp.

## WARP adaptation

The reference used Cloudflare WARP in local proxy mode and verified an exact `warp=on` response before exporting its media proxy. It also used different route order for video platforms and a public-address-validating gateway for isolated workers. Those are separate responsibilities: a listening proxy is not proof of WARP, and WARP is not an SSRF defense or a guarantee that YouTube accepts the request.

The new implementation intentionally starts narrower:

1. The manually dispatched source-check workflow optionally installs the official Cloudflare Ubuntu package, uses proxy mode on loopback port 40000 and verifies the tunnel. Failed verification stops the selected WARP probe; it does not silently produce a direct-route result.
2. The bounded session supervisor and connected tool worker preserve only a validated explicit source-proxy selection; local-only model workers receive no source route. Only the allowlisted metadata/weather/Gutenberg clients and the separate public-address-pinned reader/YouTube-metadata transport read `MICHIFLIN_SOURCE_PROXY`. Bot transport, model providers and private backup traffic do not opt in. Ambient proxy variables remain ignored.
3. Only the canonical `socks5://127.0.0.1:<unprivileged-port>` form is accepted, without credentials, paths or remote proxy hosts. Local DNS resolution is retained, unlike `socks5h`. Existing HTTPS host restrictions, redirect validation, certificate checks, byte limits and time budgets remain in force.
4. A configured proxy failure is reported, not silently retried directly. The manual probe can be run separately with and without WARP for an honest comparison.
5. The text reader validates arbitrary public HTTPS page destinations with a separate pinned-address transport; it is not a general extractor gateway. Before enabling media or browser workers, extend and test equivalent controls for every auxiliary request, including extractor manifests, segments and thumbnails. Never pass a proxy directly to a general extractor and assume that makes it safe.

For source diagnostics, run `python -m michiflin.diagnostics --download --reader --media`. The EPUB probe checks rights metadata, fetches actual bytes and validates the EPUB archive. Metadata alone is never a successful download. WARP does not bypass authentication, paywalls, DRM or platform terms; only permitted content should be requested.

## Release status

This migration increment is not a production launch. Production remains stopped pending the existing cryptographic administrator pairing, real-source and hosting lifecycle gates. Local/browser tests, remote workflow results and production delivery are separate evidence categories.
