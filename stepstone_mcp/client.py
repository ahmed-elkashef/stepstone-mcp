"""Provider-neutral person preferences stay outside this connector."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from .errors import SourceError
from .parsers import parse_job_html, parse_listing_html
from .transport import PublicTransport, ROBOTS_URL, robots_allowed


def location_url(zip_code: str, radius: int = 25, page: int = 1, age_days: int = 7) -> str:
    if not isinstance(zip_code, str) or not re.fullmatch(r"\d{5}", zip_code):
        raise ValueError("zip_code must contain five digits")
    if type(radius) is not int or not 1 <= radius <= 100:
        raise ValueError("radius must be an integer from 1 to 100")
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive integer")
    if type(age_days) is not int or age_days not in {0, 1, 7}:
        raise ValueError("age_days must be 0, 1 or 7")
    base = f"https://www.stepstone.de/jobs/in-{zip_code}"
    # The ordinary unparameterized URL currently displays the site's 30 km default.
    # It is not a substitute for a 25 km, age-filtered or later-page query.
    if radius == 30 and page == 1 and age_days == 0:
        return base
    query = {"radius": radius, "page": page}
    if age_days:
        query.update({"action": f"facet_selected;age;age_{age_days}", "ag": f"age_{age_days}"})
    return base + "?" + urlencode(query)


class StepStoneClient:
    def __init__(self, transport: PublicTransport | None = None):
        self.transport = transport or PublicTransport()

    def location_page(self, zip_code: str, radius: int = 25, page: int = 1, age_days: int = 7) -> dict:
        requested = location_url(zip_code, radius, page, age_days)
        return self._location_result(requested, zip_code, radius, page, age_days)

    def follow_location_page(self, zip_code: str, radius: int, age_days: int, page: int, page_url: str) -> dict:
        """Follow the exact provider link, rather than constructing a guessed cursor."""
        expected = location_url(zip_code, radius, page, age_days)
        if urlsplit(page_url).path != urlsplit(expected).path:
            raise SourceError("query_changed", "Continuation changes the postal-code path.", page_url)
        return self._location_result(page_url, zip_code, radius, page, age_days)

    def _location_result(self, requested: str, zip_code: str, radius: int, page: int, age_days: int) -> dict:
        html, final_url = self.transport.get(requested)
        if urlsplit(final_url).path != urlsplit(requested).path:
            raise SourceError("query_changed", "Provider changed the location URL; query coverage is unverified.", final_url)
        result = parse_listing_html(html, final_url, page)
        observed_radius = result["displayed_radius_km"]
        if observed_radius is not None and observed_radius != radius:
            raise SourceError("radius_changed", "Displayed radius differs from the requested radius; no silent expansion.", final_url,
                              details={"requested_radius_km": radius, "displayed_radius_km": observed_radius})
        result.update({"requested_query": {"zip_code": zip_code, "radius": radius, "page": page, "age_days": age_days},
                       "observed_at": datetime.now(timezone.utc).isoformat()})
        native_age = result["source_query_metadata"].get("age")
        age_verified = age_days == 0 or (native_age == age_days or parse_qs(urlsplit(final_url).query).get("ag") == [f"age_{age_days}"])
        result["query_envelope_verified"] = observed_radius == radius and age_verified
        if observed_radius is None:
            result["warnings"].append("Displayed radius is unverified.")
        if not age_verified:
            result["warnings"].append("The original age facet is unverified on the continuation URL.")
        if result["next_url"] and not robots_allowed(self.transport.policy(), result["next_url"]):
            result["continuation_access"] = "robots_denied"
            result["warnings"].append("The advertised next page is disallowed by current robot policy; this page is not complete market coverage.")
        else:
            result["continuation_access"] = "allowed" if result["next_url"] else "none_or_unverified"
        return result

    def details(self, job_url: str) -> dict:
        if not re.fullmatch(r"/stellenangebote--.+--\d+-inline\.html", urlsplit(job_url).path):
            raise ValueError("job_url must identify a public StepStone inline vacancy page")
        html, final_url = self.transport.get(job_url)
        result = parse_job_html(html, final_url)
        result["observed_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def capabilities(self) -> dict:
        policy = self.transport.policy()
        examples = {"plain_location_page": location_url("10115", 30, 1, 0),
                    "filtered_location_page": location_url("10115", 25, 1, 7),
                    "paginated_location_page": location_url("10115", 30, 2, 0),
                    "inline_job_details": "https://www.stepstone.de/stellenangebote--Example--123456-inline.html"}
        return {"ok": True, "policy_url": ROBOTS_URL, "http_backend": self.transport.backend,
                "policy_sha256": hashlib.sha256(policy.encode()).hexdigest(),
                "access": {key: {"example": value, "robots_allowed": robots_allowed(policy, value)} for key, value in examples.items()},
                "full_market_coverage": False, "account_or_application_actions": False,
                "offline_import": True, "person_filters": False,
                "warnings": ["Provider permissions and availability apply independently of software correctness.",
                             "Relative listing dates do not establish original publication instants."]}


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collect_inventory(client: StepStoneClient, query: dict, *, checkpoint: Path,
                      max_pages: int = 10, start: str | None = None, end: str | None = None) -> dict:
    """Bounded, resumable walk; failed pages never advance the checkpoint.

    start/end are immutable provenance only. Relative/card dates are not used to
    pretend to filter an exact time window. Consumers must verify original dates.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 1000:
        raise ValueError("max_pages must be an integer from 1 to 1000")
    if set(query) != {"zip_code", "radius", "age_days"}:
        raise ValueError("query must contain exactly zip_code, radius and age_days")
    location_url(**query)
    if (start is None) != (end is None):
        raise ValueError("start and end must both be supplied or omitted")
    if start is not None:
        try:
            first, last = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (start, end)]
        except (ValueError, AttributeError) as exc:
            raise ValueError("start/end must be timezone-aware ISO instants") from exc
        if first.tzinfo is None or last.tzinfo is None or first >= last:
            raise ValueError("start/end must be timezone-aware and start < end")
    bound = {"query": query, "window": {"start": start, "end": end, "membership_verified": False}}
    fingerprint = _fingerprint(bound)
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved.get("schema_version") != 1 or saved.get("query_fingerprint") != fingerprint:
            raise SourceError("checkpoint_mismatch", "Existing checkpoint belongs to different parameters or interval; unchanged.")
        body = {k: v for k, v in saved.items() if k != "checkpoint_sha256"}
        if saved.get("checkpoint_sha256") != _fingerprint(body):
            raise SourceError("checkpoint_corrupt", "Checkpoint integrity does not match; unchanged.")
    else:
        saved = {"schema_version": 1, "query_fingerprint": fingerprint, **bound,
                 "next_page": 1, "next_url": None, "pages": [], "jobs": [], "duplicates": 0,
                 "coverage_complete": False, "pagination_terminal": False,
                 "listing_inventory_complete": False, "interval_membership_verified": False,
                 "status": "incomplete", "last_error": None}
    def persist():
        body = {k: v for k, v in saved.items() if k != "checkpoint_sha256"}
        saved["checkpoint_sha256"] = _fingerprint(body)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
        temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(checkpoint)
    if saved["pagination_terminal"]:
        return saved
    known = {job["id"] for job in saved["jobs"]}
    for _ in range(max_pages):
        page = saved["next_page"]
        try:
            if saved.get("next_url") and hasattr(client, "follow_location_page"):
                result = client.follow_location_page(**query, page=page, page_url=saved["next_url"])
            else:
                result = client.location_page(**query, page=page)
        except SourceError as exc:
            saved.update({"status": "source_limited", "last_error": exc.to_dict()["error"]})
            persist()
            return saved
        ids = [job["id"] for job in result["jobs"]]
        page_hash = _fingerprint(ids)
        if ids and any(previous["ids_sha256"] == page_hash for previous in saved["pages"]):
            saved.update({"status": "source_limited", "last_error": {"code": "repeated_page", "page": page,
                "message": "The provider repeated a prior page; checkpoint did not advance."}})
            persist()
            return saved
        for job in result["jobs"]:
            if job["id"] in known:
                saved["duplicates"] += 1
            else:
                saved["jobs"].append(job)
                known.add(job["id"])
        saved["pages"].append({"page": page, "ids_sha256": page_hash, "url": result["url"],
                               "reported_total": result["reported_total"], "last_page": result["last_page"],
                               "malformed_cards": result["malformed_cards"], "warnings": result["warnings"],
                               "query_envelope_verified": result.get("query_envelope_verified", False)})
        saved.update({"next_page": page + 1, "next_url": result["next_url"], "status": "incomplete", "last_error": None})
        if result["pagination_terminal"]:
            totals = {p["reported_total"] for p in saved["pages"]}
            reconciled = len(totals) == 1 and next(iter(totals)) == len(saved["jobs"])
            clean = (not any(p["malformed_cards"] for p in saved["pages"]) and saved["duplicates"] == 0
                     and all(p["query_envelope_verified"] for p in saved["pages"]))
            inventory_complete = reconciled and clean
            saved.update({"pagination_terminal": True, "listing_inventory_complete": inventory_complete,
                          "coverage_complete": inventory_complete and start is None,
                          "status": ("complete" if start is None else "inventory_complete_interval_unverified")
                                    if inventory_complete else "source_limited"})
            if not inventory_complete:
                saved["last_error"] = {"code": "coverage_not_reconciled", "message": "Terminal page reached, but source totals, unique IDs or parser losses do not reconcile."}
            elif start is not None:
                saved["last_error"] = {"code": "interval_membership_unverified", "message": "Listing pagination is complete, but original publication membership in the frozen interval has not been verified."}
            persist()
            return saved
        if not result["next_url"]:
            saved.update({"status": "source_limited", "last_error": {"code": "continuation_missing", "page": page,
                "message": "No verified next-page URL; incomplete inventory preserved."}})
            persist()
            return saved
        persist()
    return saved
