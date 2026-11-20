# Verified library discovery and delivery

## Sources, not promises

The normal Gutenberg path uses the Project Gutenberg content mirror at `https://gutenberg.pglaf.org`, not automated page scraping of `www.gutenberg.org`. The mirror's [README](https://gutenberg.pglaf.org/README) describes the numbered source tree and generated `cache/epub` files. Its [feeds directory](https://gutenberg.pglaf.org/cache/epub/feeds/) provides the compressed public catalogue. [rOpenSci's gutenbergr documentation](https://docs.ropensci.org/gutenbergr/) also identifies this mirror for programmatic downloads. Project Gutenberg attribution and the ebook's original licence notices are preserved; no headers or content are stripped.

Gutendex-compatible record shapes remain an internal adapter interface, but the normal service and connected assistant book tool do not depend on Gutendex availability. The human-facing Gutenberg source link remains available. Open Library remains a separate catalogue/loan/read-online source; it is not converted into a supposedly free download source.

## Discovery

- On the first search, fetch the mirror's `pg_catalog.csv.gz` with a 25-second network budget and an unchanged 8 MiB compressed limit. Gzip CRC/truncation checks apply, and decompressed CSV is limited to 32 MiB.
- Build a local SQLite index atomically, with at most 150,000 feed rows and bounded fields. Only text records are indexed. Partial downloads or invalid feeds never replace a usable index.
- Search title and author separately, with case/accent folding, and support subject/bookshelf-category and language filtering. Queries are bound SQL parameters, never executable query fragments. Results are ordered by catalogue identifier, not claimed popularity.
- Return up to six candidates per page. Fetch each candidate's actual RDF metadata with at most three concurrent reads and eight seconds per read. The catalogue CSV alone is **not** evidence of public-domain status or a downloadable format.
- Require matching ebook identity and an unambiguous `Public domain in the USA.` rights statement. Advertise only format links actually present in that ebook's RDF metadata. Requested formats are filtered after verification; a page may therefore contain fewer than six matches. Candidate count is not presented as a guaranteed downloadable total.
- Refresh the public index after seven days. If refresh fails, an existing readable copy may be used with a stale-copy notice. Metadata failures that leave some verified results are explicitly disclosed; total metadata failure is an error, not a fabricated empty catalogue.

The validated compressed public feed is saved privately beside the rebuildable SQLite index. Authenticated identity/workspace snapshots may carry this optional feed (at most 8 MiB), its SHA-256 digest and its original fetch time, but never need to carry the larger public SQLite index. Existing snapshots without a feed still restore. A fresh restored feed rebuilds the index locally instead of downloading the whole catalogue on every hosting restart. A stale restored feed is disclosed as stale if network refresh fails; restoring never makes old catalogue data look fresh. Corrupt cached feeds cannot bypass the parser and may be replaced only by a newly validated feed. Rights and formats still require fresh connected per-book RDF, so this is not an offline ebook collection.

The feed-only 25-second budget stays within the transport's existing reviewed maximum. A measured 5.6 MB cold transfer took 19.4 seconds before index construction, leaving almost no margin within the previous 20-second feed budget. File downloads retain their separate 20-second budget and unchanged size cap. Diagnostics report source elapsed time and a cached index rebuild separately; a timeout remains a failed probe, not an automatic retry or an invented result.

## Delivery

An explicit user confirmation selects a book and format, not an arbitrary remote URL. Each new delivery fetches fresh RDF metadata again and rechecks identity, US public-domain status and declared formats. The smallest declared file for the selected MIME type is preferred, often an image-free EPUB for lower transfer cost; the format is not invented and the book is not recompressed or edited.

Only reviewed source-file URL patterns map to the fixed mirror: generated ebook routes, generated cache files, or identifier-derived numbered source paths. Queries, credentials, custom ports, traversal and unrelated hosts are rejected. All network redirects must stay on the fixed mirror host and still pass public-address validation, numeric-IP pinning and original-host TLS verification. WARP, if explicitly selected, receives only that checked numeric destination. There is no silent direct-route fallback.

Files remain limited to 24 MiB and a 20-second fetch budget. Compressed HTTP bodies have separate encoded/decoded bounds. EPUB ZIP structure and mimetype, text encoding/content, PDF signature or MOBI signature are checked before creating a private staged file. The original per-identity and global delivery quotas remain in force.

The durable outbox binds each receipt to the requesting cryptographic identity, chat and miniapp instance. Repeating an already handled request does not fetch or queue another file. Uncertain sends are not automatically retried. Submission to Delta Chat is not a recipient delivery receipt; actual receipt must be checked separately in live validation.

Public-domain status varies by jurisdiction. A US public-domain determination is not a worldwide legal guarantee.

## Validation boundaries

Unit tests cover mirror URL mapping, source/redirect restrictions, XML entities, mismatched identities, rights, smaller declared formats, compressed-feed limits, SQL parameterization, atomic refresh, stale/partial disclosure and delivery replay. Source diagnostics perform an actual catalogue query and fetch and validate a real EPUB. A successful source probe alone is not an encrypted mailbox delivery test or a production launch.
