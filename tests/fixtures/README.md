`ctrip_final_sanitized.json` contains the business-field allowlist extracted from
the final successful SHA–CJU one-way response in the user-supplied 2026-10-12 HAR.
It preserves 18 itineraries and 41 price offers for offline regression tests.

`ctrip_roundtrip_final_sanitized.json` contains the business-field allowlist from
an explicitly authorized live query on 2026-10-12 at 14:46 Asia/Shanghai:
SHA–KUL, outbound 2026-10-14, return 2026-10-17, one adult, economy search.
The native complete-combination mode (`isbuildup=1`, request `segmentNo=0`)
returned 1,292 complete round-trip itineraries and 3,871 price offers with
`data.context.finished == true`. Each itinerary preserves both directions.
The user-supplied round-trip HAR omitted the large search response bodies;
this fixture therefore comes from the observed live response, not that HAR.

No HAR, request headers, cookies, user identifiers, search identifiers,
routeSearchToken, searchCriteriaToken, policyToken or other credentials are
included. Correlation tests use synthetic identifiers. Tests never contact the
website, start a real browser, or send email.
