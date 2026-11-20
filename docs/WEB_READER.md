# Low-bandwidth web reader

`web.read` is a public-member operation, not a model tool. It reads an explicitly supplied public HTTPS URL and returns lightweight text through the normal identity-bound request ledger. No AI call, login, cookie jar, browser engine or temporary administrator grant is required.

## Interaction

Open **Web reader / Lector web**, paste a public HTTPS page and choose **Read page / Leer página**. The connected bot fetches the page and returns a fetched-copy timestamp, title, source URL, plain-text pages and up to 20 followable links. Page switching is local; following a link is another explicit bot request. It does not recursively crawl a site.

The text copy can be exported as `michiflin-reading.txt`. In Delta Chat this explicitly prepares a native chat draft; the client still controls recipient selection and sending. Export never constitutes a delivery receipt. In a standalone browser, already available text can be downloaded locally, but new connected fetches remain disabled without the bot.

Requests and results are stored in the requesting identity's existing service ledger and miniapp activity. The target website necessarily receives a server-side request. Do not submit private, signed, password-reset or otherwise sensitive links. Neither local-only assistant mode nor the privacy promise for local file inputs applies to an explicitly connected reader request.

## Network boundaries

- HTTPS only, standard port 443, no embedded credentials, control characters or custom request headers. Fragment identifiers are discarded before fetching. Unicode paths and hostnames are normalized.
- Every DNS result must be public unicast. Loopback, private, link-local, shared, reserved and multicast addresses are rejected, as are mapped/transition IPv6 forms and IPv6 addresses outside the global-unicast range. Ambiguous legacy numeric hostnames must still pass resolution checks.
- A short isolated resolver subprocess has no inherited runtime credential environment and a maximum five-second resolution budget. Mixed public/private DNS answers fail closed.
- The HTTP connection targets one vetted numeric address, while TLS certificate checking, SNI and the Host header retain the original hostname. Redirects receive fresh validation and resolution. Rebinding to a private address is rejected before connecting.
- Optional `MICHIFLIN_SOURCE_PROXY` uses the same canonical local SOCKS endpoint as source diagnostics. SOCKS receives the vetted numeric destination, never an unvalidated proxy-resolved hostname. A failed proxy connection is not silently retried directly.
- At most five destination requests, within one 25-second fetch budget, with five-second socket timeouts. A deadline watchdog shuts down the socket to interrupt slow response reads. No cookie, Authorization or Referer header is retained across destinations. The standard library also bounds HTTP status/header line lengths and header count.
- Encoded and decoded bodies are each limited to 1,000,000 bytes. Only identity and validated single-stream gzip encoding are accepted. Compression expansion, truncated streams, mismatched declared lengths and unsupported content types are rejected.

This is not yet a general media gateway. A future extractor must route **all** manifests, media segments and auxiliary requests through equivalent validated transport; simply passing WARP to an extractor would not provide these guarantees.

## Text boundaries

The supported source types are HTML, XHTML-as-HTML and plain text, with UTF-8, ASCII, Latin-1 or Windows-1252 response encodings. Parsing does not execute JavaScript, load CSS, evaluate XML entities, fetch images or honor a page's alternate base URL.

The reader favors substantive `main`/`article` text and otherwise falls back to body text. Scripts, styles, templates, navigation, forms and explicitly hidden content are omitted. It is a lightweight heuristic, not a full browser, accessibility-tree implementation or guaranteed article extractor. Dynamic pages can fail with an explicit no-readable-text result. Access denials are reported rather than bypassed.

HTML is capped at 20,000 element starts and a depth of 256. Returned text is limited to 36,000 characters and split into pages of at most 6,000 characters. Shortening is disclosed. Link targets are normalized when presented and fully resolved/validated only when explicitly requested. Text and source titles are rendered as text nodes, never inserted as source HTML.

## Validation

Unit coverage includes private and transition addresses, mixed DNS results, redirect rebinding, TLS hostname versus pinned IP, proxy selection, cookie/header isolation, deadline checks, compression bombs, incomplete bodies and parser limits. Bilingual Chromium/WebKit tests exercise the actual service, local pagination, explicit link following, safe title rendering, text export, reload and offline behavior.

`python -m michiflin.diagnostics --reader` adds an actual public-page probe. The manually dispatched source workflow can run it directly or through verified WARP. Live source diagnostics and synthetic regression fixtures are distinct evidence; neither is a production mailbox delivery test.
