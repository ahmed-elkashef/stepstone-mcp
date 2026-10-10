"""Synthetic tests of provenance, loss prevention, access and resume boundaries."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from stepstone_mcp.client import StepStoneClient, collect_inventory, location_url
from stepstone_mcp.errors import SourceError
from stepstone_mcp.parsers import parse_job_html, parse_listing_html, publication
from stepstone_mcp.transport import PublicTransport, ROBOTS_URL, robots_allowed

LISTING = "https://www.stepstone.de/jobs/in-10115"
JOB = "https://www.stepstone.de/stellenangebote--Synthetic-Example--123456-inline.html"
POLICY = "User-agent: *\nDisallow: /jobs/*?*\nDisallow: /public-api/\nDisallow: */application/\n"


def listing(page=1, last=2, identifier=None, total=2):
    identifier = identifier or 100 + page
    return f'''<h1>{total} Treffer für 10115 im Umkreis von 30 km</h1>
      <div id="app-unifiedResultlist"><article data-testid="job-item" id="job-item-{identifier}">
      <a data-at="job-item-title" href="/stellenangebote--Synthetic--{identifier}-inline.html">Example job</a>
      <span data-at="job-item-company-name">Synthetic employer</span>
      <span data-at="job-item-location">Berlin</span><span data-at="job-item-location">Hamburg</span>
      <span data-at="job-item-timeago">vor 2 Stunden</span><div data-at="jobcard-content">Listing snippet only</div>
      </article></div><a aria-current="true" href="/jobs/in-10115?page={page}">{page}</a>
      <a href="/jobs/in-10115?page={last}">{last}</a>
      {f'<a href="/jobs/in-10115?page={page+1}">Next</a>' if page < last else ''}'''


def detail(**updates):
    payload = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Synthetic job", "url": JOB,
               "datePosted": "2026-10-09T20:00:00+02:00", "validThrough": "2026-10-15T18:00:00Z",
               "description": "<p>" + "Full source sentence. " * 400 + "END_OF_SOURCE</p>",
               "employmentType": ["FULL_TIME", "PART_TIME"], "jobLocationType": "TELECOMMUTE",
               "jobLocation": [{"address": {"addressLocality": "Munich", "addressCountry": "DE"}},
                               {"address": {"addressLocality": "Frankfurt am Main", "addressCountry": "DE"}}],
               "hiringOrganization": {"name": "Synthetic company"}, "baseSalary": {"currency": "EUR"},
               "applicantLocationRequirements": [{"@type": "Country", "name": "Germany"}]}
    payload.update(updates)
    return '<script type="application/ld+json">' + json.dumps({"@graph": [payload]}) + '</script>'


def transport(handler):
    return PublicTransport(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


def test_full_details_are_not_truncated_and_all_worker_locations_remain():
    result = parse_job_html(detail(), JOB)
    assert len(result["description"]) > 8000
    assert result["description"].endswith("END_OF_SOURCE")
    assert result["employment_type"] == ["FULL_TIME", "PART_TIME"]
    assert len(result["locations"]) == 2
    assert result["applicant_location_requirements"][0]["name"] == "Germany"
    assert result["publication"]["instant_utc"] == "2026-10-09T18:00:00+00:00"
    assert result["description_truncated_by_connector"] is False


@pytest.mark.parametrize("value,precision,verified", [
    ("2026-10-09", "calendar_day", False), ("vor 2 Stunden", "unknown", False),
    ("2026-10-09T18:00:00", "timestamp", False), ("2026-02-30", "unknown", False),
    (None, "unknown", False), ("2026-10-09T18:00:00Z", "timestamp", True)])
def test_no_invented_publication_instants(value, precision, verified):
    result = publication(value)
    assert result["original_value"] == value
    assert result["precision"] == precision and result["timezone_verified"] == verified
    if not verified:
        assert result["instant_utc"] is None


def test_wrong_vacancy_is_not_used_as_full_details():
    with pytest.raises(SourceError, match="identity differs"):
        parse_job_html(detail(url=JOB.replace("123456", "999999")), JOB)


def test_missing_full_description_does_not_fall_back_to_related_jobs_or_page_menu():
    with pytest.raises(SourceError) as error:
        parse_job_html('<main>Menu<article data-testid="job-item">Other job</article></main>', JOB)
    assert error.value.code == "description_missing"


def test_listing_metadata_and_pagination_are_preserved_separately_from_full_text():
    result = parse_listing_html(listing(), LISTING, 1)
    assert result["page"] == 1 and result["last_page"] == 2
    assert result["next_url"].endswith("?page=2")
    assert result["jobs"][0]["id"] == "101"
    assert result["jobs"][0]["locations"] == ["Berlin", "Hamburg"]
    assert result["jobs"][0]["description"] == ""
    assert result["jobs"][0]["publication"]["instant_utc"] is None
    assert result["coverage_complete"] is False


def with_native_state(html, *, radius=30, count=2, ids=(101,), next_page=2):
    state = {'user': {'secret': 'DO_NOT_RETAIN'}, 'searchResults': {
        'resultFilterInfo': {'numberOfResults': count},
        'categorization': {'radius': radius, 'age': 7, 'orderBy': 'relevance', 'userId': 'DO_NOT_RETAIN'},
        'unifiedPagination': {'page': 1, 'pageCount': 2, 'links': {'next': f'/jobs/in-10115?siteId=250&action=paging_next&page={next_page}'}},
        'items': [{'id': identifier, 'datePosted': '2026-10-09T10:00:00Z',
                   'publishFromDate': '2026-10-09T12:00:00Z', 'publishToDate': '2026-11-01T00:00:00Z',
                   'workFromHome': '2', 'cvToJobScore': 'DO_NOT_RETAIN', 'harmonisedId': 'synthetic-stable-key'} for identifier in ids]}}
    return html + '<script>window.__PRELOADED_STATE__["app-unifiedResultlist"] = ' + json.dumps(state) + '; throw new Error("MUST_NOT_EXECUTE");</script>'


def test_embedded_native_metadata_is_matched_by_id_and_does_not_retain_user_state():
    result = parse_listing_html(with_native_state(listing()), LISTING)
    assert result['jobs'][0]['publication']['original_value'] == '2026-10-09T10:00:00Z'
    assert result['jobs'][0]['source_date_fields']['publishFromDate'] == '2026-10-09T12:00:00Z'
    assert result['jobs'][0]['work_from_home_code'] == '2'
    assert result['jobs'][0]['harmonised_id'] == 'synthetic-stable-key'
    assert result['next_url'].endswith('siteId=250&action=paging_next&page=2')
    assert result['source_query_metadata']['orderBy'] == 'relevance'
    assert 'DO_NOT_RETAIN' not in json.dumps(result)


def test_native_parser_losses_and_dom_conflicts_do_not_disappear():
    result = parse_listing_html(with_native_state(listing(), ids=(101,102)), LISTING)
    assert result['malformed_cards'] == [{'id':'102','reason':'native_record_not_rendered'}]
    with pytest.raises(SourceError) as error:
        parse_listing_html(with_native_state(listing(), radius=25), LISTING)
    assert error.value.code == 'query_metadata_conflict'
    with pytest.raises(SourceError) as error:
        parse_listing_html(with_native_state(listing(), count=3), LISTING)
    assert error.value.code == 'count_metadata_conflict'
    with pytest.raises(SourceError) as error:
        parse_listing_html(with_native_state(listing(), next_page=7), LISTING)
    assert error.value.code == 'invalid_continuation'


def test_changed_layout_and_wrong_page_are_errors():
    with pytest.raises(SourceError) as error:
        parse_listing_html("<h1>Error page</h1>", LISTING)
    assert error.value.code == "layout_changed"
    with pytest.raises(SourceError) as error:
        parse_listing_html(listing(page=1), LISTING, requested_page=7)
    assert error.value.code == "wrong_page"


def test_zero_results_requires_explicit_zero_header_and_result_container():
    result = parse_listing_html('<h1>0 Treffer</h1><div id="app-unifiedResultlist"></div>', LISTING)
    assert result["jobs"] == [] and result["pagination_terminal"] is True
    with pytest.raises(SourceError) as error:
        parse_listing_html('<h1>100 Treffer</h1><div id="app-unifiedResultlist"></div>', LISTING)
    assert error.value.code == "unexplained_empty_page"


def test_malformed_card_is_explicit_not_silently_discarded():
    html = listing().replace('</div><a aria-current', '<article data-testid="job-item">Broken</article></div><a aria-current')
    result = parse_listing_html(html, LISTING)
    assert len(result["malformed_cards"]) == 1


def test_longest_robots_rule_and_allow_ties_and_specific_agent():
    rules = 'User-agent: *\nDisallow: /jobs/*?*\nAllow: /jobs/*?q=*\nDisallow: /jobs/*?q*&*\n'
    assert not robots_allowed(rules, LISTING + '?page=2')
    assert robots_allowed(rules, LISTING + '?q=example')
    assert not robots_allowed(rules, LISTING + '?q=example&page=2')
    assert robots_allowed('User-agent: *\nDisallow: /jobs/\nAllow: /jobs/\n', LISTING)
    assert not robots_allowed('User-agent: *\nAllow: /\nUser-agent: StepStoneMCP\nDisallow: /jobs/\n', LISTING)


def test_prohibited_query_never_reaches_page_network():
    calls = []
    def handle(request):
        calls.append(str(request.url))
        assert str(request.url) == ROBOTS_URL
        return httpx.Response(200, text=POLICY)
    current = transport(handle)
    with pytest.raises(SourceError) as error:
        StepStoneClient(current).location_page("60311", 25, 7, 7)
    assert error.value.code == "robots_denied"
    assert calls == [ROBOTS_URL]


def test_robot_policy_failure_is_fail_closed():
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(503)
    with pytest.raises(SourceError) as error:
        transport(handle).get(JOB)
    assert error.value.code == "robots_unavailable" and calls == [ROBOTS_URL]


def test_provider_block_and_rate_limit_are_explicit_and_not_retried():
    for code in (403, 429):
        calls = []
        def handle(request):
            calls.append(str(request.url))
            if str(request.url) == ROBOTS_URL:
                return httpx.Response(200, text=POLICY)
            return httpx.Response(code, headers={"Retry-After": "120"})
        current = transport(handle)
        with pytest.raises(SourceError) as error:
            current.get(JOB)
        assert error.value.code == ("provider_blocked" if code == 403 else "rate_limited")
        if code == 429:
            assert error.value.retry_after_seconds == 120
            with pytest.raises(SourceError):
                current.get(JOB)
        assert calls == [ROBOTS_URL, JOB]


def test_redirect_to_disallowed_or_external_url_is_never_followed():
    for target in (LISTING + '?page=2', 'https://example.org/private'):
        calls = []
        def handle(request):
            calls.append(str(request.url))
            return httpx.Response(200, text=POLICY) if str(request.url) == ROBOTS_URL else httpx.Response(302, headers={"Location": target})
        with pytest.raises(SourceError):
            transport(handle).get(JOB)
        assert calls == [ROBOTS_URL, JOB]


def test_actual_network_timeout_is_not_empty_success():
    def handle(request):
        if str(request.url) == ROBOTS_URL:
            return httpx.Response(200, text=POLICY)
        raise httpx.ReadTimeout("synthetic timeout", request=request)
    with pytest.raises(SourceError) as error:
        transport(handle).get(JOB)
    assert error.value.code == "timeout"


def test_node_backend_uses_same_policy_and_never_requests_disallowed_page(monkeypatch):
    import stepstone_mcp.transport as module
    calls = []
    monkeypatch.setattr(module.shutil, 'which', lambda _: '/synthetic/node')
    def run(args, **kwargs):
        calls.append(args)
        assert args[2] == ROBOTS_URL
        assert args[4] == module.USER_AGENT
        return SimpleNamespace(returncode=0, stdout=json.dumps({'status': 200, 'body': POLICY, 'headers': {}}))
    monkeypatch.setattr(module.subprocess, 'run', run)
    current = PublicTransport(backend='node', min_interval=0)
    with pytest.raises(SourceError) as error:
        current.get(LISTING + '?page=7')
    assert error.value.code == 'robots_denied' and len(calls) == 1


def test_node_backend_returns_provider403_without_fallback(monkeypatch):
    import stepstone_mcp.transport as module
    calls = []
    monkeypatch.setattr(module.shutil, 'which', lambda _: '/synthetic/node')
    def run(args, **kwargs):
        calls.append(args[2])
        payload = {'status': 200, 'body': POLICY, 'headers': {}} if args[2] == ROBOTS_URL else {'status': 403, 'body': 'Denied', 'headers': {}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(SourceError) as error:
        PublicTransport(backend='node', min_interval=0).get(JOB)
    assert error.value.code == 'provider_blocked' and calls == [ROBOTS_URL, JOB]


class SyntheticPages:
    def __init__(self, *, fail_page=None, repeat=False, total=7):
        self.fail_page, self.repeat, self.total, self.calls = fail_page, repeat, total, []

    def location_page(self, zip_code, radius, age_days, page):
        self.calls.append(page)
        if page == self.fail_page:
            raise SourceError("http_error", "Synthetic provider page failed.", status_code=503)
        result = parse_listing_html(listing(page=page, last=7, identifier=101 if self.repeat else 100 + page, total=self.total), LISTING, page)
        result["query_envelope_verified"] = True
        return result

    def follow_location_page(self, zip_code, radius, age_days, page, page_url):
        assert page_url == LISTING + f'?page={page}'
        return self.location_page(zip_code, radius, age_days, page)


QUERY = {"zip_code": "10115", "radius": 30, "age_days": 0}


def test_page7_failure_resumes_original_page_and_preserves_previous6(tmp_path):
    path = tmp_path / 'inventory.checkpoint.json'
    first = SyntheticPages(fail_page=7)
    result = collect_inventory(first, QUERY, checkpoint=path)
    assert first.calls == list(range(1, 8))
    assert len(result["jobs"]) == 6 and result["next_page"] == 7
    assert result["coverage_complete"] is False and result["status"] == "source_limited"
    assert result["last_error"]["status_code"] == 503
    resumed = SyntheticPages()
    result = collect_inventory(resumed, QUERY, checkpoint=path)
    assert resumed.calls == [7] and len(result["jobs"]) == 7
    assert result["coverage_complete"] is True


def test_checkpoint_mismatch_does_not_fetch_or_overwrite(tmp_path):
    path = tmp_path / 'inventory.checkpoint.json'
    collect_inventory(SyntheticPages(), QUERY, checkpoint=path, max_pages=1,
                      start="2026-10-01T00:00:00Z", end="2026-10-08T00:00:00Z")
    before = path.read_bytes()
    current = SyntheticPages()
    with pytest.raises(SourceError) as error:
        collect_inventory(current, QUERY, checkpoint=path, start="2026-10-02T00:00:00Z", end="2026-10-09T00:00:00Z")
    assert error.value.code == "checkpoint_mismatch" and current.calls == []
    assert path.read_bytes() == before


def test_complete_listing_does_not_certify_frozen_interval_membership(tmp_path):
    result = collect_inventory(SyntheticPages(), QUERY, checkpoint=tmp_path / 'interval.json',
                               start='2026-10-01T00:00:00Z', end='2026-10-08T00:00:00Z')
    assert result['pagination_terminal'] is True and result['listing_inventory_complete'] is True
    assert result['interval_membership_verified'] is False and result['coverage_complete'] is False
    assert result['status'] == 'inventory_complete_interval_unverified'
    assert result['last_error']['code'] == 'interval_membership_unverified'


def test_corrupt_checkpoint_is_rejected(tmp_path):
    path = tmp_path / 'inventory.checkpoint.json'
    collect_inventory(SyntheticPages(), QUERY, checkpoint=path, max_pages=1)
    payload = json.loads(path.read_text())
    payload['next_page'] = 7
    path.write_text(json.dumps(payload))
    with pytest.raises(SourceError) as error:
        collect_inventory(SyntheticPages(), QUERY, checkpoint=path)
    assert error.value.code == "checkpoint_corrupt"


def test_repeated_page_and_count_drift_do_not_certify_full_inventory(tmp_path):
    result = collect_inventory(SyntheticPages(repeat=True), QUERY, checkpoint=tmp_path / 'repeat.json')
    assert result["next_page"] == 2 and len(result["jobs"]) == 1
    assert result["last_error"]["code"] == "repeated_page"
    result = collect_inventory(SyntheticPages(total=8), QUERY, checkpoint=tmp_path / 'drift.json')
    assert result["pagination_terminal"] is True and result["coverage_complete"] is False


def test_legacy_location_request_keeps_exact_geography_no_30km_substitution():
    assert '?radius=25&page=7' in location_url('60311', 25, 7, 7)
    assert location_url('61381', 30, 1, 0) == 'https://www.stepstone.de/jobs/in-61381'
    for value in (True, 0, 101, 2.5):
        with pytest.raises(ValueError):
            location_url('10115', value)


def test_mcp_error_flag_matches_structured_error(monkeypatch):
    import stepstone_mcp.server as server
    def denied(*args, **kwargs):
        raise SourceError('robots_denied', 'Synthetic forbidden URL.')
    monkeypatch.setattr(server.client, 'location_page', denied)
    result = asyncio.run(server.call_tool('search_location_page', {'zip_code': '60311'}))
    assert result.isError is True
    assert result.structuredContent['error']['code'] == 'robots_denied'
    assert json.loads(result.content[0].text)['ok'] is False


def test_mcp_full_description_is_not_shortened():
    import stepstone_mcp.server as server
    result = asyncio.run(server.call_tool('parse_job_html', {'html': detail(), 'source_url': JOB}))
    assert result.isError is False
    assert result.structuredContent['description'].endswith('END_OF_SOURCE')
