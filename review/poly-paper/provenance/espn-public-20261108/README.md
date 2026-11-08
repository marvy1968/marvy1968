# Bounded public historical enrichment — incomplete provider side

Separate supplement to the unchanged partial evidence archive. This is current public historical reconstruction, NOT archived at-fill provider payloads. All local row labels are synthetic; public game/player identifiers are observed. Runtime market strings remain unverified claims.

## Findings
- ESPN event 401909938 independently identifies Denver Nuggets (7, away) vs Indiana Pacers (11, home), NBA/basketball; listed start November 7, 2026, 4:00 PM CST. Neutral-site flag true. Public boxscore associates Nikola Jokic (3112335) with Denver and Pascal Siakam (3149673) with Indiana.
- ESPN event 401909939 independently identifies Brooklyn Nets (17, away) vs Atlanta Hawks (1, home), NBA/basketball; listed start November 7, 2026, 6:30 PM CST. Public boxscore associates CJ McCollum (2490149) with Atlanta.
- All three exact observed market-slug lookups at documented Gamma /markets/slug/{slug} returned HTTP 404 `slug not found`. This is not a 403 block and was not retried. It does NOT prove the runtime mapped market never existed: prior runtime client describes Polymarket US gateway data, whereas Gamma is a distinct public surface. No namespace equivalence or translated slug is assumed.

## Match / mismatch / missing
Current ESPN evidence independently resolves the two source-key event IDs and selected player-team associations. No independently returned mapped-provider event exists in these reads. Thus zero complete matches, zero established mismatches, and three provider-side-unverifiable examples. Do not assert valid recent-fill positive parity from this supplement.

Missing: provider canonical event/league/participants/start, independent prop question/outcomes/token availability, exact at-fill source/provider payloads and fetch times. ESPN published date/time is listed/scheduled start; actual tipoff was not established. No start-time tolerance or cross-provider start delta can be measured. Runtime key resolves to a current public game but historical intent remains reconstructed, not direct at-fill evidence. Profit and player name were not used to select or infer a different game.

## Provenance and handling
Public official ESPN event/boxscore pages only; no internal ESPN endpoint calls or website server API discovery. Only game-content facts were extracted from downloaded official public HTML; no embedded config or tracking objects included. Official Polymarket docs establish the Gamma lookup method. Eight reads total including documentation, no polling/retries; no accounts, credentials, Odds API, bot imports, VM access/changes, bets or regrading.

public-source-events.json records whitelisted public facts with field paths, URLs, CST fetch times and exact original HTTP response hashes. example-assessments.json links synthetic row labels to evidence and explicit null provider fields. provenance.json records each attempted request and hash semantics. Raw pages, configs, DBs and logs are not bundled. SHA256SUMS covers exact sanitized exported files. This supplement does not overwrite the prior bundle.
