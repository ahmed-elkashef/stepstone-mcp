# StepStone MCP Connector

Read-only StepStone HTML parsing and access through MCP or a JSON CLI. It keeps
full vacancy text, structured employment types, all workplaces, original date
values and explicit source limits. No account, application or messaging tools.

## Current access scope

Checked **10 October 2026** against the provider's [robots.txt](https://www.stepstone.de/robots.txt)
and [current user terms](https://www.stepstone.de/e-recruiting/en/legal/terms-of-use-for-users/).
The terms page links the [November 2025 terms PDF](https://www.stepstone.de/wp-content/uploads/2026/03/Terms-of-service_Stepstone_06.11.2025.pdf).

| Route | Current result |
| --- | --- |
| Unparameterized location page, such as `/jobs/in-10115` | Robot policy allows the bare URL. The site currently defaults to **30 km**, with no date filter. One page is supplemental evidence. |
| Radius, age and pagination query URLs | Current robot policy prohibits these routes. The connector returns `robots_denied` before requesting them. |
| Public inline vacancy page without query parameters | Robot policy currently allows the route; provider availability and user terms still apply. |
| Supplied HTML | Offline parsing is available. The caller must obtain and use content through an authorized route. |

The software cannot turn a provider restriction into complete market access.
There is no proxy rotation, browser impersonation, captcha solving, cookie import,
private API fallback or use of the `q` robot exception to evade pagination rules.
`get_capabilities` rechecks current robot policy instead of assuming this table is
permanent. It makes no separate contractual permission claim.

## Install

Python 3.10 or newer:

```sh
git clone https://github.com/ahmed-elkashef/stepstone-mcp.git
cd stepstone-mcp
python -m venv .venv
.venv/bin/python -m pip install -e .
```

MCP client configuration:

```json
{
  "mcpServers": {
    "stepstone": {
      "command": "/absolute/path/to/stepstone-mcp/.venv/bin/stepstone-mcp"
    }
  }
}
```

## Tools

- `get_capabilities`: current robot policy and route-level access report.
- `search_location_page`: `zip_code`, `radius`, `page`, `age_days`. Exact requested
  parameters are preserved; blocked 25 km searches are never widened to 30 km.
- `get_job_details`: `job_url`, or a stored `session_id` and `job_index`/unambiguous
  `job_query`. A direct stable vacancy URL avoids depending on a prior search.
- `parse_listing_html`: caller-supplied `html`, `source_url`, optional
  `requested_page`.
- `parse_job_html`: caller-supplied `html`, `source_url`.

Success and error results contain JSON in text and `structuredContent`.
Retrieval failures set MCP `isError=true` and `coverage_complete=false`; they are
never successful zero-result pages. Robot policy failures, missing containers,
wrong job/page identity, HTTP errors, challenges, timeouts and rate limits have
distinct error codes.

The parser uses the vacancy's matching `JobPosting` structured record and scoped
advert sections. It does not shorten descriptions or fall back to menus and
related jobs. HTML/JSON-LD is preserved alongside plain text. Missing employment
types, locations, pay or dates remain missing. A relative date or a date without
a timezone is never converted into an invented publication instant.
Listing pages also contain a native JSON record. When available, it is matched
by exact vacancy ID and supplies original `datePosted`, separate publication/
expiry fields, salary, benefits, harmonised ID, the raw home-office code and
explicit query/pagination metadata. The connector never executes page scripts
and omits user state, recommendation scores and tracking URL parameters.
Undocumented home-office codes stay raw; they are not inferred worker eligibility.

## CLI

```sh
.venv/bin/stepstone-inspect capabilities
.venv/bin/stepstone-inspect location 10115 --radius 30 --age-days 0
.venv/bin/stepstone-inspect parse-listing authorized-listing.html https://www.stepstone.de/jobs/in-10115 --page 1
.venv/bin/stepstone-inspect parse-job authorized-vacancy.html https://www.stepstone.de/stellenangebote--Example--123456-inline.html
```

`inventory` keeps an integrity-checked checkpoint and resumes the original failed
page. It stops on missing/repeated pages, malformed card losses, count drift or
access restrictions. Start/end are immutable interval provenance; listing
relative dates do **not** prove exact window membership.

```sh
.venv/bin/stepstone-inspect inventory 10115 --radius 25 --age-days 7 \
  --checkpoint inventory.checkpoint.json --max-pages 10 \
  --start 2026-10-01T00:00:00Z --end 2026-10-08T00:00:00Z
```

Under the current robot policy that inventory command stops at page 1 with an
explicit restriction. Even when future allowed pages can be traversed, terminal
pagination is recorded separately from a reconciled listing inventory and exact
interval membership. A clean walk
requires consistent totals, unique IDs and no parser losses. It is never a
promise of an immutable provider snapshot or candidate suitability.
If start/end are supplied, even an exhausted, reconciled listing walk returns
`inventory_complete_interval_unverified` with `coverage_complete=false` until
original publication dates are verified outside this listing collector.
Continuation follows the exact provider URL and rechecks query/radius evidence;
lost facets do not become a complete filtered inventory.

HTTP GET requests use an identified user agent, a bounded 15-second default
request timeout, a maximum 8 MiB response, a request throttle and up to three
validated redirects. `Retry-After` is returned and enforced for the process;
the connector does not retry denial/challenge/rate-limit responses. A failed
robots check stops access. Checkpoints and source records remain with the caller.

The default HTTP backend is `httpx`. An optional ordinary Node.js 18+ fetch
backend supports environments where the provider's network edge times out with
the Python HTTP stack. Select it explicitly with `STEPSTONE_HTTP_BACKEND=node`
(or the same setting in the MCP client's `env`). Both backends use the same
identified user agent, robot check, cookie-free GETs, redirect validation and
error boundaries. There is no automatic fallback after a denial or challenge.

## Development

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/python -m build
```

The test suite uses synthetic source records and mocked HTTP. It verifies full
text preservation, multi-location/mixed employment metadata, exact date
provenance, robot matching, blocked requests and redirects, errors, rate cooldown,
false-empty prevention, page 7 resume, immutable checkpoint bounds and MCP error
flags, native/DOM reconciliation and exclusion of user state. Live tests are deliberately bounded and do not run in CI. Current source
availability is separate from fixture correctness.

## Credits and license

This is an **independent implementation**, informed by the public
[kdkiss/mcp-stepstone](https://github.com/kdkiss/mcp-stepstone) project and its
contributors. Its README at commit
[`cb3d7f8f8d985499547db6f0095d74e5b9e11e7c`](https://github.com/kdkiss/mcp-stepstone/tree/cb3d7f8f8d985499547db6f0095d74e5b9e11e7c)
declares MIT. That checkout has no standalone license file. This release copies
no upstream implementation or recorded job HTML, and does not claim to be a
GitHub fork or official StepStone software. See [NOTICE](NOTICE).

New source is MIT licensed. The license does not license provider content or
grant access to its services. The repository contains reusable code and synthetic
tests only; no personal profiles, credentials, private workflow state or harvested
job datasets.
