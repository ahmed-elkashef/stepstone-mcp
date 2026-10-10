"""Parse full supplied HTML without invented dates or description truncation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from .errors import SourceError
from .transport import validate_url


def text(node) -> str:
    if node is None:
        return ""
    for item in node.select("script,style,noscript"):
        item.decompose()
    return node.get_text("\n", strip=True)


def stable_id(url: str, fallback: str = "") -> str:
    match = re.search(r"--([0-9]+)-inline\.html$", urlsplit(url).path)
    if match:
        return match.group(1)
    match = re.fullmatch(r"(?:job-item-)?([0-9]+)", fallback)
    return match.group(1) if match else ""


def publication(value) -> dict:
    """Preserve the provider value; never give calendar/relative dates a UTC time."""
    out = {"original_value": value, "instant_utc": None, "precision": "unknown", "timezone_verified": False}
    if not isinstance(value, str) or not value.strip():
        return out
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            out["precision"] = "calendar_day"
        except ValueError:
            pass
        return out
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return out
    out["precision"] = "timestamp"
    if instant.tzinfo is not None:
        out["instant_utc"] = instant.astimezone(timezone.utc).isoformat()
        out["timezone_verified"] = True
    return out


def _schema_objects(value):
    if isinstance(value, dict):
        yield value
        if isinstance(value.get("@graph"), list):
            for node in value["@graph"]:
                yield from _schema_objects(node)
    elif isinstance(value, list):
        for node in value:
            yield from _schema_objects(node)


def parse_listing_html(html: str, source_url: str, requested_page: int | None = None) -> dict:
    validate_url(source_url)
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#app-unifiedResultlist")
    if container is None:
        raise SourceError("layout_changed", "Listing container is missing; not a successful empty page.", source_url)
    head = text(soup.h1)
    count = re.search(r"([\d.]+)\s+(?:Treffer|Jobs|Stellenangebote)", head)
    total = int(count.group(1).replace(".", "")) if count else None
    radius_match = re.search(r"(?:Umkreis von|Radius)\s+(\d+)\s*km", head)
    radius = int(radius_match.group(1)) if radius_match else None
    current = soup.select_one('a[aria-current="true"][href*="page="]')
    observed_page = None
    if current:
        raw = parse_qs(urlsplit(current["href"]).query).get("page", [""])[0]
        observed_page = int(raw) if raw.isdigit() else None
    if requested_page is not None and observed_page is not None and requested_page != observed_page:
        raise SourceError("wrong_page", "Provider displayed a different page than requested.", source_url,
                          details={"requested_page": requested_page, "observed_page": observed_page})
    page = observed_page or requested_page or 1
    links = {}
    for anchor in soup.select('a[href*="page="]'):
        absolute = urljoin(source_url, anchor["href"])
        if urlsplit(absolute).path != urlsplit(source_url).path:
            continue
        raw = parse_qs(urlsplit(absolute).query).get("page", [""])[0]
        if raw.isdigit() and int(raw) >= 1:
            links[int(raw)] = absolute
    last = max(links) if links else (1 if total == 0 else None)
    jobs, malformed = [], []
    seen = set()
    duplicate_cards = 0
    for index, article in enumerate(container.select('article[data-testid="job-item"]')):
        anchor = article.select_one('a[data-at="job-item-title"][href]') or article.select_one('a[href*="/stellenangebote--"][href]')
        if anchor is None:
            malformed.append({"index": index, "reason": "missing_job_url"})
            continue
        url = urljoin(source_url, anchor["href"])
        try:
            validate_url(url)
        except SourceError:
            malformed.append({"index": index, "reason": "invalid_job_url"})
            continue
        identifier = stable_id(url, article.get("id", ""))
        if not identifier:
            malformed.append({"index": index, "reason": "missing_stable_id", "url": url})
            continue
        if identifier in seen:
            duplicate_cards += 1
            continue
        seen.add(identifier)
        values = lambda selector: list(dict.fromkeys(text(e) for e in article.select(selector) if text(e)))
        location_values = values('[data-at="job-item-location"]')
        snippet = values('[data-at="jobcard-content"]')
        posted = text(article.select_one('[data-at="job-item-timeago"]'))
        company = text(article.select_one('[data-at="job-item-company-name"]'))
        jobs.append({
            "source": "stepstone", "id": identifier, "title": text(anchor), "company": company,
            "location": "; ".join(location_values), "locations": location_values,
            "posted": posted, "publication": publication(posted),
            "url": url, "link": url, "description": "", "snippet": "\n".join(snippet),
            "description_complete": False,
            "employment_labels": values('[data-at="job-item-employment-type"], [data-at="job-item-job-type"]'),
            "badges": values('[data-at="job-item-badge"], [data-at="job-item-top-label"]'),
        })
    if not jobs and total != 0:
        raise SourceError("unexplained_empty_page", "No valid job cards and no verified zero-result header; not terminal.", source_url,
                          details={"reported_total": total, "malformed_cards": malformed})
    next_url = links.get(page + 1)
    warnings = []
    if last is None:
        warnings.append("Pagination boundary is unverified; no complete inventory claim.")
    if last is not None and page < last and not next_url:
        warnings.append("The next page link is missing; no invented continuation URL.")
    if observed_page is None:
        warnings.append("The displayed page number is unverified.")
    return {
        "ok": True, "status": "retrieved", "url": source_url, "page": page,
        "last_page": last, "next_url": next_url, "reported_total": total,
        "displayed_radius_km": radius, "query_heading": head, "jobs": jobs,
        "malformed_cards": malformed, "duplicate_cards": duplicate_cards,
        "warnings": warnings, "coverage_complete": False,
        "pagination_terminal": bool(last is not None and page == last),
    }


def parse_job_html(html: str, source_url: str) -> dict:
    validate_url(source_url)
    soup = BeautifulSoup(html, "html.parser")
    expected_id = stable_id(source_url)
    schema = []
    invalid_schema_blocks = 0
    for block in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(block.get_text())
        except (ValueError, TypeError):
            invalid_schema_blocks += 1
            continue
        for record in _schema_objects(payload):
            types = record.get("@type", [])
            if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
                schema.append(record)
    matched = [s for s in schema if expected_id and stable_id(str(s.get("url", ""))) == expected_id]
    if matched:
        schema = matched
    elif schema and any(s.get("url") for s in schema):
        raise SourceError("wrong_job", "Structured job identity differs from the requested vacancy.", source_url)
    if len(schema) > 1:
        raise SourceError("ambiguous_job", "Multiple structured job postings remain; choose an unambiguous vacancy page.", source_url)
    original = schema[0] if schema else {}
    body = original.get("description")
    if isinstance(body, str) and body.strip():
        description = text(BeautifulSoup(body, "html.parser"))
        scope = "schema_org_JobPosting.description"
    else:
        node = soup.select_one('[data-at="job-ad-content"]') or soup.select_one('[data-testid="job-description"]')
        description = text(node)
        scope = "job_ad_content" if node else "missing"
    if not description:
        raise SourceError("description_missing", "Full job content could not be extracted; no menu or related-job fallback used.", source_url,
                          details={"invalid_schema_blocks": invalid_schema_blocks})
    def section(name):
        return text(soup.select_one(f'[data-at="section-text-{name}-content"]'))
    organisation = original.get("hiringOrganization", {})
    if not isinstance(organisation, dict):
        organisation = {}
    locations = original.get("jobLocation", [])
    if isinstance(locations, dict):
        locations = [locations]
    if not isinstance(locations, list):
        locations = []
    employment = original.get("employmentType", [])
    if isinstance(employment, str):
        employment = [employment]
    if not isinstance(employment, list):
        employment = []
    location_names = []
    for place in locations:
        if isinstance(place, dict):
            address = place.get("address", {})
            if isinstance(address, dict) and address.get("addressLocality"):
                location_names.append(str(address["addressLocality"]))
    title = original.get("title") or text(soup.select_one('[data-at="header-job-title"]') or soup.h1)
    return {
        "ok": True, "status": "retrieved", "source": "stepstone", "id": expected_id,
        "url": source_url, "job_url": source_url, "title": title,
        "company": organisation.get("name", ""), "company_details": organisation,
        "location": "; ".join(dict.fromkeys(location_names)), "locations": locations,
        "employment_type": employment, "job_location_type": original.get("jobLocationType"),
        "applicant_location_requirements": original.get("applicantLocationRequirements"),
        "salary": original.get("baseSalary"), "posted_date": original.get("datePosted"),
        "publication": publication(original.get("datePosted")),
        "valid_through": original.get("validThrough"), "expiry": publication(original.get("validThrough")),
        "description": description, "description_html": body if isinstance(body, str) else None,
        "description_characters": len(description), "description_scope": scope,
        "description_truncated_by_connector": False,
        "requirements": section("profile"), "responsibilities": section("description"),
        "benefits": section("benefits"), "introduction": section("introduction"),
        "provider_structured_record": original, "invalid_schema_blocks": invalid_schema_blocks,
        "warnings": [] if schema else ["No structured JobPosting record; publication and other absent metadata stay unverified."],
    }
