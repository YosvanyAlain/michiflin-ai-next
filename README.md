# Michiflin AI

A new bilingual workspace for Delta Chat: thoughtful local utilities, an ebook discovery library, an animated weather experience, and a local-first assistant.

**Status: active reconstruction, not a production release.** The previous service remains stopped. This repository starts with a new history and does not import the previous implementation, account state, credentials, or invitation links.

## Implemented foundations

- An independent, responsive miniapp with English and Spanish interfaces, light/dark themes, accessible navigation, and reduced-motion support.
- Thirteen utility cards: offline arithmetic, dimensional unit conversion, text tools, device-only passwords, JSON, world clocks, notes, savings projections, date differences, Unicode/Base64/URL encoding, local file checksums, private image optimization, and connected weather.
- Project Gutenberg discovery through Gutendex, with title, writer, subject, language, and verified format filters. Open Library is a separate catalogue/loan source, never advertised as a guaranteed download.
- Open-Meteo place disambiguation, modelled conditions, hourly forecasts, seven-day forecasts, and condition-driven weather effects. Saved or old data is not described as a live observation.
- A transport-independent service layer with sender-and-key-bound instances, a durable request ledger, request identity checks, public-user quotas, and fail-closed administrator permissions.
- An OpenAI-compatible model gateway that never substitutes online calls for an explicitly local request. Provider keys remain server-side and are never returned in the model catalogue.
- A bounded, versioned WebXDC wire protocol, reproducible miniapp packaging, and a single-account Delta Chat adapter with key-bound instances.

## New connected capabilities

- Explicitly confirmed Project Gutenberg delivery into an encrypted one-to-one chat. Each request rechecks source metadata and US public-domain status, validates the file type, and uses a durable per-identity delivery receipt. No arbitrary download URL is accepted. Files are limited to 24 MiB and subject to separate public-user and service-wide delivery quotas. Submission to Delta Chat is not a delivery/read receipt from the recipient.
- Optional read-only assistant tools, capped at two per message: arithmetic, unit conversion, clocks, book discovery, place lookup, and weather. No shell, file access, messages, downloads, or permission changes are exposed to models. Verified results appear in an expandable activity trace.
- A strict local-only mode that excludes external data tools and never silently switches to an online provider.
- Optional pinned CPU inference using llama.cpp and Qwen2.5-0.5B-Instruct Q4_K_M. Assets are downloaded only on explicit preparation and checked against fixed SHA-256 digests. This small local model has limited reasoning and language quality; it is not equivalent to a large online model.

## Local inference

```sh
python -m michiflin.local_model prepare
python -m michiflin.local_model serve
```

This optional Linux x86-64 setup downloads roughly 515 MB. It binds to loopback only and creates a private `local.key` file in its cache directory. Set `MICHIFLIN_LOCAL_MODEL_URL=http://127.0.0.1:8091/v1/chat/completions` and load that key into `MICHIFLIN_LOCAL_MODEL_KEY` for the bot process. Never commit or print the key. The model subprocess receives a minimal environment without provider or mailbox credentials. Model weights are provided by Qwen under its model licence; llama.cpp is distributed under MIT.

## Not ready yet

The Delta Chat adapter has native-core local tests, but private deployment scheduling, final administrator provisioning, broader media workflows, and live-device release validation are still required. Authenticated snapshot/restore commands are implemented and tested, including a new-format production seed restored without mail IO. Provider configuration migration and the actual production identity restoration have been verified separately in private storage; neither is included in this repository. A browser test is not proof of delivery in Delta Chat. A real encrypted two-account mail roundtrip has also verified public-member arithmetic and replay-safe notes on separate temporary accounts, not the production mailbox. No runtime deployment workflow is enabled in this repository. The manual public-source diagnostic workflow contains no credentials and does not start a bot.

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

Book metadata: [Gutendex](https://gutendex.com/) and [Open Library](https://openlibrary.org/developers/api). Weather: [Open-Meteo](https://open-meteo.com/). Source availability and usage terms apply. Public-domain status varies by jurisdiction.

The hero illustration is AI-generated decorative artwork. It is packaged locally and does not fetch an external image. Book placeholders are typographic, not purported official covers.
