# Michiflin AI

A new bilingual workspace for Delta Chat: thoughtful local utilities, an ebook discovery library, an animated weather experience, and a local-first assistant.

**Status: active reconstruction, not a production release.** The previous service remains stopped. This repository starts with a new history and does not import the previous implementation, account state, credentials, or invitation links.

## Implemented foundations

- An independent, responsive miniapp with English and Spanish interfaces, light/dark themes, accessible navigation, and reduced-motion support. Compact tool cards support local search and category filters.
- Eighteen utility cards: offline arithmetic, dimensional unit conversion, text tools, device-only passwords, JSON, world clocks, notes, savings projections, date differences, Unicode/Base64/URL encoding, local file checksums, private image optimization, CSV/JSON table conversion, ZIP/EPUB metadata inspection, PCM WAV editing, connected weather, a low-bandwidth public web reader, and explicitly metadata-only YouTube previews.
- Project Gutenberg discovery through its official content mirror, with a bounded local catalogue and fresh per-book rights/format checks. Title, writer, subject, language, and verified format filters are supported. See [library boundaries](docs/LIBRARY.md). Open Library is a separate catalogue/loan source, never advertised as a guaranteed download.
- Open-Meteo place disambiguation, modelled conditions, hourly forecasts, seven-day forecasts, and condition-driven weather effects. Saved or old data is not described as a live observation.
- A transport-independent service layer with sender-and-key-bound instances, a durable request ledger, request identity checks, public-user quotas, and fail-closed administrator permissions.
- An OpenAI-compatible model gateway that never substitutes online calls for an explicitly local request. Provider keys remain server-side and are never returned in the model catalogue.
- A bounded, versioned WebXDC wire protocol, reproducible miniapp packaging, and a single-account Delta Chat adapter with key-bound instances.

## New connected capabilities

- Explicitly confirmed Project Gutenberg delivery into an encrypted one-to-one chat. Each request rechecks source metadata and US public-domain status, validates the file type, and uses a durable per-identity delivery receipt. No arbitrary download URL is accepted. Files are limited to 24 MiB and subject to separate public-user and service-wide delivery quotas. Submission to Delta Chat is not a delivery/read receipt from the recipient.
- A non-AI public web reader with explicit link following, local pagination, fetched-copy provenance and user-controlled text export. Its separate HTTPS transport validates every DNS answer and redirect, pins the checked public address and preserves TLS hostname verification, including through the optional local SOCKS route. No page scripts, images, cookies or login credentials are loaded. See [reader boundaries](docs/WEB_READER.md).
- A metadata-only YouTube preview through the public oEmbed endpoint, accepting video-specific watch/Shorts/live/embed links. Titles and channel names are returned without a player, thumbnails, cookies or a model. Actual video download, duration, quality selection and transcripts are not implemented. See [media boundaries](docs/MEDIA.md).
- Optional read-only assistant tools, capped at two per message: arithmetic, unit conversion, clocks, book discovery, place lookup, and weather. No shell, file access, messages, downloads, or permission changes are exposed to models. Verified results appear in an expandable activity trace.
- A strict local-only mode that excludes external data tools and never silently switches to an online provider.
- Optional pinned CPU inference using llama.cpp and Qwen2.5-0.5B-Instruct Q4_K_M. Assets are downloaded only on explicit preparation and checked against fixed SHA-256 digests. This small local model has limited reasoning and language quality; it is not equivalent to a large online model.

## Optional source routing

The manual public-source check can opt into a verified local Cloudflare WARP proxy. Only allowlisted metadata/weather/ebook clients and the DNS-pinned reader/YouTube-metadata transport use `MICHIFLIN_SOURCE_PROXY`; bot mail, model APIs and backups are unchanged. Proxy failures do not silently fall back to direct requests. This does not implement YouTube downloads or guarantee platform access. See [migration and routing boundaries](docs/MIGRATION.md).

## Local inference

```sh
python -m michiflin.local_model prepare
python -m michiflin.local_model serve
```

This optional Linux x86-64 setup downloads roughly 515 MB. It binds to loopback only and creates a private `local.key` file in its cache directory. Set `MICHIFLIN_LOCAL_MODEL_URL=http://127.0.0.1:8091/v1/chat/completions` and load that key into `MICHIFLIN_LOCAL_MODEL_KEY` for the bot process. Never commit or print the key. The model subprocess receives a minimal environment without provider or mailbox credentials. Model weights are provided by Qwen under its model licence; llama.cpp is distributed under MIT.

## Not ready yet

The private session supervisor, authenticated shutdown checkpoints and restored restart path are implemented and tested. Private backup validation has uploaded, downloaded and independently restored the real encrypted identity without starting production mail IO. An isolated synthetic profile has also opened the actual miniapp in Delta Chat Desktop and exercised offline tools, bilingual preferences and native file-export selection. Separately, a real encrypted two-account mail roundtrip verified public-member arithmetic and replay-safe notes on temporary accounts, not the production mailbox.

Production remains stopped. Two requested administrator identities still require verified cryptographic pairing, and the legacy hosting platform has an orphan queued-run inconsistency. The official-mirror Gutenberg search and a real validated EPUB have now passed an isolated encrypted two-account delivery test, including replay without duplicate delivery; this does not certify production hosting. Broader server-side media conversion, arbitrary platform downloads and archive extraction are **not** implemented by the local file tools. No runtime deployment workflow is enabled in this public repository. No built package or browser fixture is presented as a deployed production service.

## Local files and audio

- Table lab handles quoted CSV, Unicode and JSON row arrays, with formula-injection protection enabled on CSV exports.
- Archive lens reads bounded ZIP/EPUB metadata, flags unsafe paths, symbolic links, duplicate names and suspicious expansion, and never extracts or executes content. It is not a virus scan or an integrity certificate.
- WAV atelier produces actual 16-bit PCM clips with optional peak normalization and edge fades. It supports mono/stereo WAV only, not MP3, AAC or video transcoding.
- File fingerprints, optimized images, table contents, archive inventories and audio samples stay local. User-selected inputs do not enter model prompts, bot status updates, activity history or local storage.
- Exporting inside a compatible Delta Chat client uses the native `sendToChat()` file API only after an explicit button click. The client controls recipient selection and subsequent sending; the miniapp never claims a delivery receipt. Standalone browsers offer a local download instead.

See [file-tool limits](docs/FILE_TOOLS.md) for exact supported formats and boundaries. The [capability migration inventory](docs/MIGRATION.md) distinguishes implemented tools from the media, reading and remote-file workflows still to rebuild.

## Development

Python 3.12 or newer and Node.js 22 or newer are recommended.

```sh
python -m pip install -e '.[test,browser,bot]'
python -m playwright install chromium webkit
python -m pytest -q
ruff check src tests
python -m michiflin.cli build
```

The package can be opened in Delta Chat for offline utilities. Connected operations require a configured runtime; do not treat the package as a deployed bot.

## Privacy and access

Normal users will not need temporary administrator access windows. Administrator privileges require a configured private identity **and** verified cryptographic identity, not an email address supplied by a client. Public access does not mean public administrator access.

Do not commit account databases, provider keys, personal administrator addresses, encrypted invitation URLs, or runtime logs. Production configuration belongs in private secret storage. No invitation URL is published here.

## Sources and artwork

Book catalogue and ebook metadata: [Project Gutenberg content mirror](https://gutenberg.pglaf.org/README) and [Open Library](https://openlibrary.org/developers/api). Weather: [Open-Meteo](https://open-meteo.com/). Source availability and usage terms apply. Public-domain status varies by jurisdiction.

The hero illustration is AI-generated decorative artwork. It is packaged locally and does not fetch an external image. Book placeholders are typographic, not purported official covers.
