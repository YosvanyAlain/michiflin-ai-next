# Media migration: honest first step

The connected **YouTube preview / Vista de YouTube** tool implements `media.inspect`: public video-title and channel lookup through YouTube's oEmbed endpoint. It is not yet a video downloader, transcript service, player, duration inspector or quality selector.

## What it does

- Accepts standard HTTPS YouTube watch links, youtu.be links and video-specific Shorts/live/embed links. A video ID is required; playlists and channels are rejected.
- Discards tracking, playlist and timestamp parameters and requests metadata for a canonical video URL. It never fetches a user-supplied arbitrary metadata endpoint.
- Uses the same public-address-pinned HTTPS transport as the reader, in explicit JSON mode. Every DNS result and redirect is checked; TLS still validates the original host. Optional verified local WARP routing does not change those checks or silently fall back to direct access.
- Returns a bounded title, channel name, canonical source URL and fetched timestamp. Provider embed HTML, thumbnails, author links and any claimed download information are discarded. The miniapp renders text, not an iframe or remote image.
- Uses the ordinary public-member identity binding, quotas and replay-safe ledger. No model, cookie import, login or administrator grant is involved.

A private, removed, region-restricted or host-blocked video can fail. Failure never becomes an invented preview. WARP tunnel verification and YouTube acceptance are separate tests. `python -m michiflin.diagnostics --media` probes actual metadata access, not playable video bytes.

## Still required for real media downloads

An isolated extraction worker, no inherited credentials or host filesystem access, public-address validation for every extractor/manifest/segment request, content permission and platform-terms handling, explicit output selection, duration/size limits, bounded resource use, cancellation, validated playable output and durable confirmed delivery. Authenticated cookies must never be shared between users.

No download control is displayed on the current preview. `download_available` is explicitly false. Public metadata lookup is not evidence that an extractor, a download or a production delivery works.
