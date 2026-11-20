# Michiflin AI

A new bilingual workspace for Delta Chat: thoughtful local utilities, an ebook discovery library, an animated weather experience, and a local-first assistant.

**Status: active reconstruction, not a production release.** The previous service remains stopped. This repository starts with a new history and does not import the previous implementation, account state, credentials, or invitation links.

## Implemented foundations

- An independent, responsive miniapp with English and Spanish interfaces, light/dark themes, accessible navigation, and reduced-motion support.
- Offline arithmetic, dimensional unit conversion, text tools, device-only password generation, JSON formatting, world clocks, and local notes.
- Project Gutenberg discovery through Gutendex, with title, writer, subject, language, and verified format filters. Open Library is a separate catalogue/loan source, never advertised as a guaranteed download.
- Open-Meteo place disambiguation, modelled conditions, hourly forecasts, seven-day forecasts, and condition-driven weather effects. Saved or old data is not described as a live observation.
- A transport-independent service layer with sender-and-key-bound instances, a durable request ledger, request identity checks, public-user quotas, and fail-closed administrator permissions.
- An OpenAI-compatible model gateway that never substitutes online calls for an explicitly local request. Provider keys remain server-side and are never returned in the model catalogue.
- A bounded, versioned WebXDC wire protocol reproducible miniapp packaging, and a single-account Delta Chat adapter with key-bound instances.

## Not ready yet

The Delta Chat adapter has native-core local tests, but production integration, verified administrator provisioning, encrypted backup/restore, migration of provider configuration from private storage, complete agent workflows, media/file workflows, and live-device release validation are still required. A browser test is not proof of delivery in Delta Chat. No runtime deployment workflow is enabled in this repository.

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
