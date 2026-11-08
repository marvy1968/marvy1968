# Polymarket US + ESPN: three current reconstructed identity matches

Public documentation verified: https://docs.polymarket.us/api-reference/events/get-events explicitly documents GET https://gateway.polymarket.us/v1/events, security: [], and repeated slug query filters. One unauthenticated public data read returned both candidate events with associated markets. No /v2 route, private/internal API, account, cookies, login or key was used.

All three exact observed market slugs were found uniquely inside returned events:
- synthetic-fill-01: Nikola Jokic at least 3 steals, US event 185087, market 2257469; long = Yes.
- synthetic-fill-02: Pascal Siakam at least 4 assists, US event 185087, market 2258052; short = No.
- synthetic-fill-03: CJ McCollum at least 20 combined points/rebounds/assists, US event 185088, market 2268156; long = Yes.

US event 185087 independently names Denver Nuggets and Indiana Pacers (both league nba), listed start November 7, 2026, 4:00 PM CST. ESPN event 401909938 independently supplies the same teams/start and player-team boxscore association.
US event 185088 independently names Brooklyn Nets and Atlanta Hawks (both league nba), listed start November 7, 2026, 6:30 PM CST. ESPN event 401909939 independently supplies the same teams/start and player-team association. Listed start deltas are 0 seconds in both cases.

These are CURRENT PUBLIC HISTORICAL RECONSTRUCTIONS, not archived at-fill payloads. They can support observed reconstruction parity tests but cannot certify exact original payloads or actual tipoff instants. No regrading or profit validation performed. ESPN provenance is carried forward unchanged from the earlier enrichment.

## Explicit remaining gaps and semantics
US startTime is documented as event start time and gameStartTime as game start time; these are treated as listed/scheduled starts, NOT verified actual tipoff. US teams[].ordering is absent: do not infer home/away from array order. Subject/player IDs are absent on these markets; player and threshold/stat come from explicit questions, with player-team association independently evidenced by ESPN boxscore.
US marketSides contain public side IDs, Yes/No descriptions, long flags, instrument identifiers and tradable=true, while market status is resolved. These are observed current fields, NOT assertions that a new trade is possible. No ERC1155 token ID was returned; the instrument identifier is the slug, not a token. Do not invent tokens or reject a US instrument solely because it lacks a CLOB token ID.

## Privacy and provenance
Only three selected public market identities, two parent event identity schemas, corresponding prior sanitized fill facts (synthetic account-local row labels), and source-backed ESPN facts included. Public event/market/player/side IDs are retained because they are canonical identity facts, not account-local identifiers. No raw 5.9 MB response, raw logs/DB, internal metadata, raw config, personal accounts, keys or session data is included. Every pair has URL, CST fetch timestamp, original response hash, event/market JSON hashes and JSON field paths. Documentation contract and its URL are included separately. Credential-pattern screening passed.

One gateway data request, one official documentation read and one search; no retries/polling, Gamma requests, VM access/changes, bot imports, services, bets, Odds API calls or DB writes. Prior bundles were not changed.
