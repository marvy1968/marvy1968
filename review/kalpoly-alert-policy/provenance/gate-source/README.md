# Alert gate dependency source export — review only

Exactly fourteen requested filenames under /home/moonrider1968/aj-bet-bot/ajbet were considered. Source reads only, via existing read-only SSH command runner. No runtime modules executed/imported, tests, bot/provider calls, configuration/.env/DB/log reads, deploys, restarts or VM writes. Existing archives unchanged. card_rules.py not read/exported again.

Files must be regular non-symlink files with unchanged inode/device at open; O_NOFOLLOW used. Symlinks are omitted without following or reading targets. Parent path resolution checked to remain the exact allowlisted directory. Maximum 350,000 bytes each; missing/nonregular/oversized/read-error files become explicit gaps rather than replacements. No recursive searches or other modules read.

manifest.json records each original/export SHA256, byte size, original mtime and read time in America/Chicago (CST), status and line-level sanitization reasons. Source files are complete unless noted redacted. Potential credential assignments, Telegram bot tokens/chat IDs, email contacts, private-key markers, phone-like literals and long numeric literals are screened without printing values. Private-key-marker files would be quarantined, not exported. Other flagged lines are replaced with comment placeholders, which may make a redacted source non-executable; this is evidence, not implementation. Hash differences are intentional and recorded. Empty/default/dynamic credential variable names alone are not secrets.

static-imports.json is a syntax-only import inventory from the fourteen original allowlisted files; dependencies were not opened or resolved. It includes standard-library, optional and local imports without claiming they are all missing from the parent's existing bundle. Parent/cloud should compare against the prior 79-file archive. No sensitive source values are included in the report.

Only source/, manifest.json, static-imports.json, README.md and SHA256SUMS are bundled. SHA256SUMS hashes exact sanitized delivered files; original hashes refer to original source bytes. This export does not authorize activation or any configuration change.
