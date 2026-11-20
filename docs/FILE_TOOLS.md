# Local file tools

These tools run inside the miniapp. Files must be selected by the user and are not uploaded to the bot or an AI provider. Inputs and generated file contents are not saved in activity history or local storage. Navigating away releases preview URLs; changing inputs invalidates pending reads and generated exports.

## Exports

A compatible Delta Chat client offers **Prepare a chat draft**. This calls the standard [webxdc sendToChat API](https://webxdc.org/docs/spec/sendToChat.html) with a named Blob after an explicit click. The native client owns destination selection and any later send action. The API does not report recipient delivery or whether a user cancelled. No success notification claims that a file was sent. Outside a compatible client, the same generated file is offered as a local browser download.

No export exceeds 20 MiB. Only fixed application-generated filenames are accepted by the export helper. Pending native export requests cannot be double-submitted. A failed request reports an unconfirmed outcome rather than inventing a successful delivery.

## Table lab

- UTF-8 CSV with comma, semicolon or tab separators; quoted fields, embedded newlines and escaped quotes.
- Up to 1 million input characters, 2,000 rows, 100 columns and 10,000 characters per cell. Imported files are limited to 2 million bytes.
- CSV converts to a JSON array of row arrays. JSON input must use row arrays containing strings, finite numbers, booleans or null; objects are not interpreted as executable data or schema.
- CSV export defaults to spreadsheet formula protection. Cells beginning with risky formula prefixes or leading control characters receive an apostrophe. Users can explicitly disable this transformation. JSON preserves original strings; protection is not represented as lossless CSV roundtripping.
- Empty cells and multiline Unicode are preserved. Previews are shortened; exported results are complete within the documented bounds.

## Archive lens

- Reads ZIP and EPUB central-directory metadata only, up to 20 MiB and 1,000 entries.
- Cross-checks directory bounds, local-header names and compression metadata. Truncation, overlapping entries and inconsistent structure are rejected.
- Reports unsafe paths, ambiguous duplicate names, symbolic links, encryption, unsupported compression, legacy filename encoding and suspicious declared expansion.
- Never extracts, decompresses, previews, follows archive links or executes file content. Embedded CRC values are not validated by decompressing data. An inventory with no flags is **not** a safety certificate or malware scan.
- ZIP64, multi-disk archives and other archive formats such as RAR/7z are not supported. Output reports declared sizes, not verified decompressed content.

## WAV atelier

- Uncompressed 16-bit integer PCM WAV, mono or stereo, 8–96 kHz; up to 120 seconds and 20 MiB.
- Validates RIFF bounds, PCM frame alignment, byte rate and data chunks before reading samples. Floating-point, compressed, multichannel, MP3, AAC and video inputs are rejected rather than relabelled as converted.
- Produces a new WAV containing the selected sample interval. Optional normalization targets 95% peak amplitude; an optional 0–1,000 ms fade softens clip edges. Sample rate and channel count are retained.
- Export contains a fresh minimal WAV header and PCM samples, not the input's ancillary metadata. Normalization is a peak operation, not a loudness-standard or noise-removal claim.

## Existing image and fingerprint tools

Local file SHA-256 and bounded JPEG/PNG optimization remain available. Image exports now use the same native file-draft flow when supported. Their asynchronous results are discarded if the input changes or the user leaves the view.

## Deliberate boundaries

These features do not replace server-side video/audio transcoders, platform downloaders, PDF/OCR services or archive extraction. No general-purpose shell, arbitrary download URL, hidden file upload or media provider credential is exposed to models or miniapp users. Extending that scope requires a separate bounded implementation and validation, not copying legacy code into this rebuild.
