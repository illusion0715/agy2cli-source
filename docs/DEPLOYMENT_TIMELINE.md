# AGY2API deployment evidence timeline

Read-only observation: 2026-11-20. All times below are UTC. Add eight hours for Beijing time. This document records deployment metadata only. The engineering archive has subsequently been expanded; ARCHITECTURE.md is authoritative for its current scope. No historical executable code, runtime data, credentials, private endpoints, accounts, production configuration, network mappings, or Git history is included.

## Evidence boundaries

Docker container StartedAt/FinishedAt values establish retained lifecycle records, not independent public-service acceptance. Image Created values establish image-build metadata, not deployment times. Revision strings below are version.txt metadata read from the respective retained container without starting it; they are not verified Git HEADs. Adjacent stop/start records support a replacement sequence, but no historical public health-check or release-acceptance log was established. Directory timestamps and directory names are not treated as release evidence.

## Retained container lifecycle records

| Container start UTC | Container stop UTC | AGY image tag | Recorded source revision | Image Created UTC |
|---|---|---|---|---|
| 2026-10-26 15:49:16.144 | 2026-10-27 14:53:01.666 | `patched-20261026-v2` | `7720ecf7c0a662abd768ed010d45ab27f7f7a913` | 2026-10-26 14:44:02.251 |
| 2026-11-11 12:24:46.110 | 2026-11-11 12:52:35.870 | `source-a5bd33e` | `dfcfb52b1d167020adf5307a794c2b3234043325` | 2026-11-11 12:20:58.240 |
| 2026-11-11 12:52:36.067 | 2026-11-12 20:24:07.460 | `source-8850ffafea8d` | `6ddb624ff773bc76ed5b4aadf9d8edcbbe1a5a4b` | 2026-11-11 12:52:25.711 |
| 2026-11-12 20:24:07.655 | 2026-11-16 20:26:50.743 | `source-28145a7ab604` | `509c98237670ec6d7153599454b01359d6a440e6` | 2026-11-12 20:23:46.222 |
| 2026-11-16 20:26:51.104 | 2026-11-17 20:29:47.320 | `source-f602621c6625` | `ca34911693513dabe78527c2caea0ae654723ee9` | 2026-11-16 20:26:01.392 |
| 2026-11-17 20:29:47.504 | 2026-11-18 20:24:25.742 | `source-824f0a5de9b6` | `3681773e94fcd452453fa0283d1e81342ee82315` | 2026-11-17 20:28:59.592 |
| 2026-11-18 20:24:25.936 | running at observation | `source-218ea453cf75` | `60979cdb36fee7e63769a8b0c062f2f15d6bedbd` | 2026-11-18 20:24:02.271 |

## Additional retained images, not proven deployed

- `agy2api-patched-20261026:latest`: image Created 2026-10-26 14:10:31.892 UTC. No matching retained container was established in this inspection.
- `ghcr.io/su-kaka/agy2api:latest`: image Created 2026-10-26 12:18:13.284 UTC. OCI source label points to https://github.com/su-kaka/agy2api, revision `731e81e11ea0c96d40fff59ac94b1a0bbffe1101`, version label `master`. No retained container directly using this image was established.

## Current-source and gateway caveat

The current AGY container uses `source-218ea453cf75`, image digest `sha256:324ca9bbe8118e16010ca4a595e89d18ffed4fc986c426fc7408873e2b83d1d2`. Its recorded revision is `60979cdb36fee7e63769a8b0c062f2f15d6bedbd`. The current gateway retains separate modern AGY and legacy backends. Consequently the modern container version does not identify the version serving every path of the gateway. The legacy backend is not another historical release of the current AGY source and is excluded from this AGY timeline.

The earliest retained old container stopped on 2026-10-27, followed by an evidence gap until the November retained sequence; this gap does not prove that the service was offline. No complete deployment ledger or public acceptance history was recovered. A deployment-backup directory exists, but its name and mtime alone do not establish a release; its data and contents were not archived.

## Source archive scope remains unchanged

This is still a reviewed, non-runnable source subset under the included CNC-1.0 license, not a full source or deployment backup. Authentication/credential export and validation-bypass components remain excluded. No production services or files were changed during this history inspection.
