# Security

This is a development repository, not a production deployment.

- Never put private reports, administrator addresses, account exports, tokens, or invitation links in public issues.
- Administrator identity is bound to a verified key; address matching alone is insufficient.
- A process restart turns interrupted operations into uncertain outcomes. They must not be replayed as new work.
- Book, weather, and model responses are untrusted data. They must never become executable HTML, script, commands, or arbitrary network destinations.
- Offline password generation stays entirely on the user's device and must never enter activity history, telemetry, or backups.
- Provider configuration must be migrated privately. The GitHub Secrets API does not reveal existing secret values, so a successful repository copy is not proof of secret migration.

Do not deploy until the identity adapter, encrypted backup recovery, abuse controls, and native-client end-to-end checks have been reviewed.
