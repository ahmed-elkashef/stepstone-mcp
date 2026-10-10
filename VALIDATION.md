# Release validation — 10 October 2026

This validates software behavior and bounded current availability. It does not
claim a complete German or regional job census.

- **31 synthetic tests passed** on local Python 3.14. Tests cover full text,
  multi-location and mixed worker metadata, date/timezone provenance, false-empty
  prevention, exact source-page identity, robot matching, denied redirects,
  provider errors/rate cooldown, page 7 resume, immutable checkpoints, interval
  uncertainty, both HTTP backend policy guards, embedded JSON metadata matching,
  DOM/native conflicts and exclusion of user state/recommendation scores.
- A real MCP STDIO client initialized the installed entry point, listed all five
  tools, parsed an explicitly verified zero-result page, and received
  `isError=true` with structured `description_missing` for absent full content.
- Wheel and source distributions built successfully. The wheel includes the
  optional Node fetch helper; the source archive contains no harvested HTML,
  virtual environment, credentials, checkpoint or workflow data.
- Bounded current bare location-page reads returned 25 cards each at the
  provider's **30 km default**. Pagination links were discovered and accurately
  classified as disallowed by the current robot policy. No restricted follow-up
  pages were requested.
- The 50 real captured card IDs all matched their embedded JSON records. All
  50 had timezone-bearing `datePosted` values; separate `publishFromDate` and
  `publishToDate` fields stayed distinct. Native pagination and query radius/age/
  sort metadata were preserved with zero native/rendered card losses.
- A current full vacancy page obtained through an allowed bare URL was parsed
  without truncation: 2,074 description characters, scoped requirements,
  responsibilities/benefits, one structured workplace, worker type and the
  original timezone-bearing publication instant.
- Subsequent identified live full-detail reads encountered a Python HTTP timeout
  and a Node network error. Both became explicit source limitations. Current
  source availability remains intermittent; there was no request-identity
  rotation or denial/challenge fallback.
- A current 25 km / seven-day / page 7 request was rejected by the robot-policy
  guard before its page network request. The former tool's success-shaped text
  failure is replaced by an explicit error, not a repaired full-market feed.

Source providers can change their schema, permissions and availability. Automated
CI uses synthetic records and does not contact the provider. New source failures
must remain visible as coverage limitations, separate from candidate fit.
