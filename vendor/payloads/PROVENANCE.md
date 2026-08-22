# Bundled PS5 payload provenance

These are third-party PS5 homebrew payloads. Seventeen entries were curated
from `http://45.56.67.85/umtx2/payload_map.js`; BackPork 0.1 was added from
the official BestPig GitHub release. They are not authored or relicensed by
exFAT Forge.

`manifest.json` records the exact upstream release URL, version, byte size,
and SHA-256 digest for every bundled file. `src/exfat_forge/payload_catalog.json`
records its title, authors, project repository, compatibility guidance, and
description. Refer to each linked upstream project for its source code and
license terms.

Maintainers refresh the set with:

```text
python tools/sync_bundled_payloads.py
```

The application verifies each digest before releasing a payload from the
one-file executable into the user's payload folder.
