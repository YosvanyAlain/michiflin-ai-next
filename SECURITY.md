# Security

This is a development repository, not a production deployment.

- Never put private reports, administrator addresses, account exports, tokens, or invitation links in public issues.
- Administrator identity is bound to a verified key; address matching alone is insufficient.
- A process restart turns interrupted operations into uncertain outcomes. They must not be replayed as new work.
- Book, weather, and model responses are untrusted data. They must never become executable HTML, script, commands, or arbitrary network destinations.
- Offline password generation stays entirely on the user's device and must never enter activity history, telemetry, or backups.
- Local file inputs are user-selected, bounded and ephemeral. They are not assistant tools and must not enter the bot transport or activity storage. Native exports require a separate explicit user action and never imply delivery.
- ZIP metadata inspection does not decompress content or certify it as safe. Audio editing accepts only bounded PCM WAV, and CSV exports protect spreadsheet formulas by default.
- Provider configuration must be migrated privately. The GitHub Secrets API does not reveal existing secret values, so a successful repository copy is not proof of secret migration.

Do not deploy until the identity adapter, encrypted backup recovery, abuse controls, and native-client end-to-end checks have been reviewed.
