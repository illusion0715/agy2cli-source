# Reviewed source subset

This private archival snapshot contains selected source files from [agy2api](https://github.com/su-kaka/agy2api). Original copyright and the Cooperative Non-Commercial License (CNC-1.0) are retained in LICENSE. This archive is intended for non-commercial preservation under that license.

This is **not a complete or runnable backup**. The included files are unchanged request-data schemas, a rough token estimator, generic async streaming-response helpers, a browser base-path helper, and static PostCSS/Tailwind configuration. See SOURCE_MANIFEST.json for exact content hashes and the recorded version caveat.

Excluded entirely: OAuth/login flows and validation relaxation; signature-validation bypass or placeholder logic; authentication and credential management/import/export; provider request adapters and converters; anti-truncation logic; database and runtime/logging code; executable entrypoints and deployment/install scripts; environment files and examples; cookies, sessions, access/refresh tokens, API keys, credentials, databases and logs; binaries, archives, built assets, runtime data, deployment/personal identifiers, and all previous Git history. No provider calls, source execution, builds, or service changes were performed for this archival snapshot.

The omitted components must not be inferred to exist in this repository. Restore or deployment from this subset is unsupported.
