"""Small JSON CLI with local import and resumable inventory modes."""

import argparse
import json
import os
from pathlib import Path

from .client import StepStoneClient, collect_inventory
from .errors import SourceError
from .parsers import parse_job_html, parse_listing_html
from .transport import PublicTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    for command in ("location", "inventory"):
        current = sub.add_parser(command)
        current.add_argument("zip_code")
        current.add_argument("--radius", type=int, default=25)
        current.add_argument("--age-days", type=int, choices=(0, 1, 7), default=7)
        if command == "location":
            current.add_argument("--page", type=int, default=1)
        else:
            current.add_argument("--checkpoint", type=Path, required=True)
            current.add_argument("--max-pages", type=int, default=10)
            current.add_argument("--start")
            current.add_argument("--end")
    current = sub.add_parser("details")
    current.add_argument("job_url")
    for command in ("parse-listing", "parse-job"):
        current = sub.add_parser(command)
        current.add_argument("html_file", type=Path)
        current.add_argument("source_url")
        if command == "parse-listing":
            current.add_argument("--page", type=int)
    args = parser.parse_args()
    client = None
    try:
        if args.command == "parse-listing":
            result = parse_listing_html(args.html_file.read_text(), args.source_url, args.page)
        elif args.command == "parse-job":
            result = parse_job_html(args.html_file.read_text(), args.source_url)
        else:
            client = StepStoneClient(PublicTransport(backend=os.environ.get("STEPSTONE_HTTP_BACKEND", "httpx")))
            if args.command == "capabilities":
                result = client.capabilities()
            elif args.command == "details":
                result = client.details(args.job_url)
            elif args.command == "location":
                result = client.location_page(args.zip_code, args.radius, args.page, args.age_days)
            else:
                result = collect_inventory(client, {"zip_code": args.zip_code, "radius": args.radius, "age_days": args.age_days},
                    checkpoint=args.checkpoint, max_pages=args.max_pages, start=args.start, end=args.end)
    except SourceError as exc:
        result = exc.to_dict()
    except (OSError, ValueError) as exc:
        result = {"ok": False, "status": "invalid_request", "error": {"code": "invalid_request", "message": str(exc)}}
    finally:
        if client:
            client.transport.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok") is False or result.get("status") in {"source_limited", "inventory_complete_interval_unverified"}:
        raise SystemExit(2)
