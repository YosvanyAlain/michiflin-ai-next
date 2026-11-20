# Private operations and release gates

The public repository contains code and validation only. Runtime credentials, administrator pins, account exports, recovery keys and invitation URLs belong exclusively in private storage. No user needs a temporary access grant for ordinary public use.

## Account migration

1. Stop the previous runtime and confirm its scheduled workflow is disabled.
2. Authenticate the encrypted source snapshot before extracting anything.
3. Restore into a new private directory. Never overwrite or reset the original account.
4. Run a SQLite integrity check and compare the restored public-key fingerprint to the privately retained identity. Do not print the invitation URL.
5. Initialize a clean workspace database. Do not import executable legacy application code or previous instance permissions.
6. Pin administrators only after verifying their native cryptographic identities. An address that has not been paired and verified remains pending, even when it is on the owner's desired administrator list.
7. Create a new-format encrypted snapshot and independently restore it. Preserve legacy recovery assets separately.

This reconstruction's actual account identity and provider migration have been checked in isolated private storage without starting mail IO. That does not certify a running production deployment.

## Snapshot commands

Install the `bot` extra. Stop the runtime first. The snapshot command refuses an active runtime lock and never starts mail IO.

```sh
python -m michiflin.cli snapshot --accounts /private/runtime/accounts --state /private/runtime/state --output /private/next.snapshot --passphrase-file /private/backup.key
python -m michiflin.cli restore --input /private/next.snapshot --output /private/restoration-test --passphrase-file /private/backup.key
```

The passphrase file must have owner-only permissions, not be a symlink, and contain at least 24 characters. Snapshots combine a native encrypted account export, a consistent SQLite workspace copy, staged delivery files, and an authenticated inventory. The outer archive uses streaming AES-256-GCM with a fresh salt and nonce and a scrypt-derived key. Restoration checks authentication, archive paths, file digests, SQLite integrity, and the native account key before making the new destination visible. Existing destinations are never overwritten. Logs and provider configuration are not packed into this archive.

Queued deliveries can resume only after the restored owner, chat and instance bindings pass again. A delivery whose sending outcome was interrupted is uncertain and is never automatically sent again.

## Final launch gates

- Green source, browser, native-core, package, and restore checks on the exact revision.
- At least one verified online model and an explicitly identified, tested local model when local mode is offered.
- Reachability of the actual configured data sources from the intended runtime network. A public-source diagnostic may fail during upstream maintenance; that is not converted into a fabricated result or a silently substituted source.
- Real encrypted roundtrip using a separate client, plus a native miniapp interaction. Synthetic status updates and browser fixtures are not sufficient.
- Private scheduled deployment, exclusive account ownership, bounded uptime, authenticated backup at shutdown, restore verification, and a tested restart path.
- No invitation, administrator address, provider credential or account log in a public job or artifact.

Do not interpret a built `.xdc`, a restored account, or a green local test suite as approval to bypass the remaining gates.
