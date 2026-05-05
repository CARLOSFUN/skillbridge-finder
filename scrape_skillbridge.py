#!/usr/bin/env python3
"""
scrape_skillbridge.py — DoD SkillBridge Opportunity Finder

USAGE (just run this — setup is automatic):
    ./run.sh                                               # interactive guided mode
    python scrape_skillbridge.py --search "cyber"              # keyword search
    python scrape_skillbridge.py --industry "Technology"       # filter by industry
    python scrape_skillbridge.py --list-industries             # show industry list
    python scrape_skillbridge.py --state TX                    # filter by state
    python scrape_skillbridge.py --search "IT" --state CA      # combine filters
    python scrape_skillbridge.py --refresh                     # force fresh download
    python scrape_skillbridge.py -o results.csv                # save all to file
    python scrape_skillbridge.py --industry "Healthcare" -o out.csv
    python scrape_skillbridge.py --format json -o out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable

try:
    import certifi
    import requests
    import truststore
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError as _err:
    print(f"""
  [ERROR] Missing required package: {_err.name}

  Use the launcher instead of running this script directly:

      ./run.sh

  Then run the program again.
""")
    sys.exit(1)

truststore.inject_into_ssl()

API_URL = "https://api.skillbridge.mil/Organizations/Authorized"
CACHE_FILE = Path(__file__).parent / "skillbridge_cache.json"
CACHE_MAX_AGE_DAYS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.skillbridge.mil/organizations",
}

CA_BUNDLE = certifi.where()
CSV_FIELDS = ["id", "name", "industry", "state", "website", "mou_expiration"]

# Industry auto-tagging: ordered list of (label, regex patterns).
# Checked top-to-bottom; first match wins.
# Patterns use \b word boundaries to prevent partial-word false positives
# (e.g. "hospital" must not match "hospitality", "care" must not match "autocare").
# A trailing wildcard (\w*) allows prefix matching where we want stem coverage.
INDUSTRY_MAP: list[tuple[str, list[str]]] = [
    ("Aerospace / Aviation", [
        r"\baerospace\b", r"\baviation\b", r"\baircraft\b", r"\bairline\b",
        r"\bboeing\b", r"\blockheed\b", r"\braytheon\b", r"\bnorthrop\b",
    ]),
    ("Technology / IT / Cyber", [
        r"\bcyber\w*", r"\bsoftware\b", r"\binformation technology\b",
        r"\bdigital\b", r"\bcloud\b", r"\bnetwork\w*", r"\bcomputer\w*",
        r"\bmachine learning\b", r"\banalytics\b", r"\bsaas\b",
        r"\bdevops\b", r"\bcoding\b", r"\bprogramming\b", r"\btech\w*",
        r"\b(it)\b",
    ]),
    ("Defense / Intelligence", [
        r"\bdefense\b", r"\bdefence\b", r"\bintelligence\b",
        r"\bhomeland\b", r"\bnational security\b", r"\bdod\b",
        r"\bgovernment contractor\b",
    ]),
    ("Healthcare / Medical", [
        r"\bhealthcare\b", r"\bhealth\b", r"\bhospital\b", r"\bmedical\b",
        r"\bclinic\w*", r"\bnursing\b", r"\bpharm\w*", r"\bbiotech\b",
        r"\bwellness\b", r"\btherapeutic\b", r"\bdental\b",
        r"\bveterinary\b", r"\bveterinarian\b",
    ]),
    ("Logistics / Supply Chain", [
        r"\blogistic\w*", r"\bsupply chain\b", r"\bshipping\b",
        r"\bfreight\b", r"\bwarehouse\b", r"\bdistribution\b",
        r"\btransport\w*", r"\bfleet\b", r"\bfulfillment\b",
    ]),
    ("Finance / Banking", [
        r"\bfinance\b", r"\bfinancial\b", r"\bbanking\b", r"\binsurance\b",
        r"\binvestment\b", r"\baccounting\b", r"\bcapital\b",
        r"\bcredit\b", r"\basset management\b",
    ]),
    ("Engineering / Manufacturing", [
        r"\bengineering\b", r"\bmanufactur\w*", r"\bindustrial\b",
        r"\bconstruction\b", r"\benergy\b", r"\butilities\b",
        r"\belectric\w*", r"\bnuclear\b",
    ]),
    ("Law Enforcement / Security", [
        r"\blaw enforcement\b", r"\bpolice\b", r"\bcorrections\b",
        r"\bprotective services\b", r"\binvestigation\b",
        r"\bsecurity services\b",
    ]),
    ("Education / Training", [
        r"\beducation\b", r"\btraining\b", r"\buniversity\b",
        r"\bcollege\b", r"\bschool\b", r"\bacademy\b",
        r"\blearning\b", r"\binstitute\b",
    ]),
    ("Consulting / Staffing", [
        r"\bconsult\w*", r"\bstaffing\b", r"\badvisory\b",
        r"\bprofessional services\b", r"\bhuman resources\b",
    ]),
    ("Retail / Hospitality", [
        r"\bretail\b", r"\bhospitality\b", r"\brestaurant\b",
        r"\bhotel\b", r"\bfood\b", r"\bbeverage\b",
        r"\bgrocery\b", r"\bconsumer\b",
    ]),
]

INDUSTRY_LABELS: list[str] = [label for label, _ in INDUSTRY_MAP] + ["Other"]

# Pre-compile all patterns once for efficiency.
_COMPILED_MAP: list[tuple[str, list[re.Pattern[str]]]] = [
    (label, [re.compile(p, re.IGNORECASE) for p in patterns])
    for label, patterns in INDUSTRY_MAP
]


def tag_industry(name: str) -> str:
    """Return the best-matching industry label for an org name."""
    for label, patterns in _COMPILED_MAP:
        if any(p.search(name) for p in patterns):
            return label
    return "Other"


@dataclass
class Organization:
    id: str | int | None
    name: str
    industry: str
    state: str | None
    website: str | None
    mou_expiration: str | None


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


# ── HTTP session ──────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = CA_BUNDLE

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ── API fetch ─────────────────────────────────────────────────────────────────

def build_params(
    start: int,
    length: int,
    col_matrix: str | None = "0-Name,1-MouExpDate",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "draw": 1,
        "order[0][column]": 0,
        "order[0][dir]": "asc",
        "start": start,
        "length": length,
        "search[value]": "",
        "search[regex]": "false",
    }
    if col_matrix is not None:
        params["colMatrix"] = col_matrix
    return params


def fetch_page(
    session: requests.Session,
    start: int,
    length: int,
    timeout: int = 30,
    col_matrix: str | None = "0-Name,1-MouExpDate",
) -> dict[str, Any]:
    response = session.get(
        API_URL,
        params=build_params(start, length, col_matrix),
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("API response was not valid JSON.") from exc


def normalize_organization(raw: dict[str, Any]) -> Organization:
    raw_state = (
        raw.get("state")
        or raw.get("st")
        or raw.get("stateCode")
        or raw.get("stateAbbr")
    )
    state = clean_string(raw_state)
    if state and len(state) == 2:
        state = state.upper()

    name = clean_string(raw.get("name")) or ""
    return Organization(
        id=raw.get("id"),
        name=name,
        industry=tag_industry(name),
        state=state,
        website=clean_string(raw.get("url")),
        mou_expiration=clean_date(raw.get("mexd")),
    )


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def clean_date(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned[:10] if cleaned else None


def iter_organizations(
    session: requests.Session,
    page_size: int,
    delay: float,
    limit: int | None = None,
) -> Generator[Organization, None, None]:
    logging.info("Fetching first page (page size: %s) …", page_size)

    first_page = fetch_page(session=session, start=0, length=page_size)
    total_records = int(first_page.get("recordsTotal", 0))

    if total_records <= 0:
        logging.warning("API returned zero records.")
        return

    if limit:
        total_records = min(total_records, limit)
        logging.info("Collecting up to %s records.", total_records)
    else:
        logging.info("Server reports %s authorized organizations.", total_records)

    yielded = 0

    for raw_org in first_page.get("data", []):
        yield normalize_organization(raw_org)
        yielded += 1
        if yielded >= total_records:
            return

    start = page_size

    while start < total_records:
        end = min(start + page_size, total_records)
        logging.info("Fetching records %s–%s …", start + 1, end)

        if delay > 0:
            time.sleep(delay)

        page = fetch_page(session=session, start=start, length=page_size)
        rows = page.get("data", [])

        if not rows:
            logging.warning("Empty page at offset %s.", start)
            break

        for raw_org in rows:
            yield normalize_organization(raw_org)
            yielded += 1
            if yielded >= total_records:
                return

        start += page_size


# ── Field discovery ───────────────────────────────────────────────────────────

# Column matrices to probe — each tests a different set of possible server columns.
_PROBE_MATRICES: list[tuple[str, str | None]] = [
    ("baseline (name + expiry)", "0-Name,1-MouExpDate"),
    ("no colMatrix (server defaults)", None),
    (
        "extended attempt",
        (
            "0-Name,1-MouExpDate,2-Industry,3-Sector,4-OpportunityType,"
            "5-Location,6-State,7-City,8-Description,9-Duration,"
            "10-JobCategory,11-Type,12-Program"
        ),
    ),
]


def dump_raw_fields(session: requests.Session) -> int:
    """Probe the API with several colMatrix values and report every field returned."""
    print("\n  Probing the SkillBridge API for available data fields …\n")

    seen_keys: set[str] = set()
    variants: list[dict[str, Any]] = []

    for label, col_matrix in _PROBE_MATRICES:
        print(f"  [{label}]")
        try:
            page = fetch_page(session, start=0, length=3, col_matrix=col_matrix)
            records: list[dict[str, Any]] = page.get("data", [])

            if not records:
                print("    → no records returned\n")
                continue

            new_keys = {k for rec in records for k in rec} - seen_keys
            seen_keys |= new_keys

            if new_keys:
                print(f"    → {len(new_keys)} new field(s) found: {sorted(new_keys)}")
            else:
                print("    → no new fields beyond what was already seen")

            variants.append({"col_matrix": col_matrix, "sample": records[0]})

        except Exception as exc:
            print(f"    → error: {exc}")

        print()

    # --- Full field breakdown with sample values ---
    print(f"  {'─' * 60}")
    print(f"  All unique fields across all probes ({len(seen_keys)} total):\n")

    # Merge all sample records to maximise value coverage
    merged_sample: dict[str, Any] = {}
    for v in variants:
        for k, val in v["sample"].items():
            if k not in merged_sample or merged_sample[k] in (None, "", []):
                merged_sample[k] = val

    for key in sorted(seen_keys):
        raw_val = merged_sample.get(key, "<absent>")
        display = repr(str(raw_val)[:70])
        print(f"    {key:<22} → {display}")

    # --- Save full dump for offline inspection ---
    dump_path = Path("skillbridge_raw_dump.json")
    with dump_path.open("w", encoding="utf-8") as f:
        json.dump(
            {"all_fields": sorted(seen_keys), "variants": variants},
            f,
            indent=2,
        )

    print(f"\n  Full raw response saved to: {dump_path}")
    print(
        "  Review that file (or share it) to see exactly which fields\n"
        "  the API exposes so we can build precise industry filtering.\n"
    )
    return 0


# ── Cache ─────────────────────────────────────────────────────────────────────

def load_cache() -> list[Organization] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        fetched_str = data["fetched_at"].replace("Z", "+00:00")
        fetched_at = datetime.fromisoformat(fetched_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - fetched_at).days
        if age_days > CACHE_MAX_AGE_DAYS:
            logging.info("Cache is %d days old — will refresh.", age_days)
            return None

        orgs = []
        for item in data["organizations"]:
            name = item.get("name", "")
            orgs.append(Organization(
                id=item.get("id"),
                name=name,
                # Re-tag if cache predates the industry field.
                industry=item.get("industry") or tag_industry(name),
                state=item.get("state"),
                website=item.get("website"),
                mou_expiration=item.get("mou_expiration"),
            ))
        logging.info(
            "Loaded %d organizations from local cache (%d day(s) old).",
            len(orgs),
            age_days,
        )
        return orgs

    except Exception as exc:
        logging.debug("Cache unreadable (%s) — fetching fresh data.", exc)
        return None


def save_cache(organizations: list[Organization]) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "organizations": [asdict(org) for org in organizations],
    }
    try:
        with CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logging.debug("Saved %d organizations to cache.", len(organizations))
    except Exception as exc:
        logging.warning("Could not save cache: %s", exc)


def get_organizations(
    session: requests.Session,
    page_size: int,
    delay: float,
    limit: int | None = None,
    refresh: bool = False,
) -> list[Organization]:
    if not refresh:
        cached = load_cache()
        if cached is not None:
            return cached

    organizations = list(
        iter_organizations(session=session, page_size=page_size, delay=delay, limit=limit)
    )

    # Only cache a full (unlimited) download.
    if organizations and limit is None:
        save_cache(organizations)

    return organizations


# ── Filter ────────────────────────────────────────────────────────────────────

def filter_organizations(
    organizations: list[Organization],
    search: str | None = None,
    state: str | None = None,
    industry: str | None = None,
) -> list[Organization]:
    results = organizations

    if search:
        kw = search.lower()
        results = [o for o in results if kw in o.name.lower()]

    if state:
        target = state.upper().strip()
        results = [o for o in results if o.state and o.state.upper() == target]

    if industry:
        target = industry.lower().strip()
        results = [o for o in results if target in o.industry.lower()]

    return results


# ── Terminal table ────────────────────────────────────────────────────────────

def print_table(organizations: list[Organization]) -> None:
    if not organizations:
        print("\n  No matching organizations found.\n")
        return

    def truncate(s: str, width: int) -> str:
        return (s[: width - 1] + "…") if len(s) > width else s

    name_w = min(40, max(len(o.name) for o in organizations))
    ind_w  = min(28, max(len(o.industry) for o in organizations))
    web_w  = min(34, max(len(o.website or "") for o in organizations))

    def make_row(num: Any, name: str, industry: str, website: str, expiry: str) -> str:
        return (
            f"  {str(num):>4} | "
            f"{name:<{name_w}} | "
            f"{industry:<{ind_w}} | "
            f"{website:<{web_w}} | "
            f"{expiry}"
        )

    header = make_row("#", "Organization Name", "Industry", "Website", "MOU Expires")
    sep = "  " + "-" * (len(header) - 2)

    print(f"\n  Found {len(organizations)} organization(s):\n")
    print(header)
    print(sep)

    for i, org in enumerate(organizations, 1):
        print(make_row(
            i,
            truncate(org.name, name_w),
            truncate(org.industry, ind_w),
            truncate(org.website or "", web_w),
            org.mou_expiration or "N/A",
        ))

    print()


# ── Output writers ────────────────────────────────────────────────────────────

def write_csv(output_path: Path, organizations: Iterable[Organization]) -> int:
    count = 0
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for org in organizations:
            writer.writerow(asdict(org))
            count += 1
    return count


def write_json(output_path: Path, organizations: Iterable[Organization]) -> int:
    data = [asdict(org) for org in organizations]
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return len(data)


def save_results(
    results: list[Organization],
    output: str,
    fmt: str,
) -> None:
    out_path = Path(output)
    if fmt == "json" or out_path.suffix.lower() == ".json":
        count = write_json(out_path, results)
    else:
        if out_path.suffix.lower() not in (".csv",):
            out_path = out_path.with_suffix(".csv")
        count = write_csv(out_path, results)
    print(f"\n  Saved {count} record(s) to {out_path}\n")


# ── Banner ────────────────────────────────────────────────────────────────────

def print_banner() -> None:
    print(r"""
  ███████╗██╗  ██╗██╗██╗     ██╗     ██████╗ ██████╗ ██╗██████╗  ██████╗ ███████╗
  ██╔════╝██║ ██╔╝██║██║     ██║     ██╔══██╗██╔══██╗██║██╔══██╗██╔════╝ ██╔════╝
  ███████╗█████╔╝ ██║██║     ██║     ██████╔╝██████╔╝██║██║  ██║██║  ███╗█████╗
  ╚════██║██╔═██╗ ██║██║     ██║     ██╔══██╗██╔══██╗██║██║  ██║██║   ██║██╔══╝
  ███████║██║  ██╗██║███████╗███████╗██████╔╝██║  ██║██║██████╔╝╚██████╔╝███████╗
  ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝  ╚═════╝ ╚══════╝
  

          DoD SkillBridge Authorized Organizations — Opportunity Finder
          Helping service members find civilian career internships
    """)


# ── Interactive mode ──────────────────────────────────────────────────────────

def run_interactive(
    session: requests.Session,
    page_size: int,
    delay: float,
    refresh: bool,
) -> int:
    print("  Industry categories:\n")
    for i, label in enumerate(INDUSTRY_LABELS, 1):
        print(f"    {i:>2}. {label}")
    print()

    try:
        ind_choice = input(
            "  Pick an industry number (or press Enter to skip): "
        ).strip()
        industry: str | None = None
        if ind_choice.isdigit():
            idx = int(ind_choice) - 1
            if 0 <= idx < len(INDUSTRY_LABELS):
                industry = INDUSTRY_LABELS[idx]
            else:
                print("  Invalid number — skipping industry filter.")
        elif ind_choice:
            industry = ind_choice  # allow free-text too

        search = input(
            "  Keyword in company name (or Enter to skip): "
        ).strip() or None
        state = input(
            "  State code (e.g. TX, CA — or Enter to skip): "
        ).strip() or None
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 0

    print()
    print("  Fetching data (this may take a moment the first time) …")

    try:
        organizations = get_organizations(
            session=session,
            page_size=page_size,
            delay=delay,
            refresh=refresh,
        )
    except requests.RequestException as exc:
        logging.error("HTTP request failed: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("Runtime error: %s", exc)
        return 1

    if not organizations:
        logging.error("No records collected.")
        return 1

    results = filter_organizations(organizations, search=search, state=state, industry=industry)
    print_table(results)

    if results:
        try:
            dest = input(
                "  Save to file? (Enter a filename like results.csv, or press Enter to skip): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            dest = ""

        if dest:
            fmt = "json" if dest.endswith(".json") else "csv"
            save_results(results, dest, fmt)

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find DoD SkillBridge authorized organizations.",
        epilog=(
            "Run with no arguments for interactive guided mode.\n\n"
            "Examples:\n"
            "  python scrape_skillbridge.py\n"
            "  python scrape_skillbridge.py --search 'cyber'\n"
            "  python scrape_skillbridge.py --state TX\n"
            "  python scrape_skillbridge.py --search 'IT' --state CA\n"
            "  python scrape_skillbridge.py --list-industries\n"
            "  python scrape_skillbridge.py --industry 'Technology'\n"
            "  python scrape_skillbridge.py --industry 'Healthcare' --state TX\n"
            "  python scrape_skillbridge.py --search 'logistics' -o results.csv\n"
            "  python scrape_skillbridge.py --refresh\n"
            "  python scrape_skillbridge.py --dump-fields\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--search",
        metavar="KEYWORD",
        help="Filter organizations whose name contains KEYWORD.",
    )
    parser.add_argument(
        "--industry",
        metavar="INDUSTRY",
        help=(
            "Filter by industry category (e.g. 'Technology', 'Healthcare'). "
            "Partial match is fine. Use --list-industries to see all options."
        ),
    )
    parser.add_argument(
        "--list-industries",
        action="store_true",
        help="Print all available industry categories and exit.",
    )
    parser.add_argument(
        "--state",
        metavar="ST",
        help="Filter by 2-letter state code (e.g. TX, CA, VA).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the local cache and download fresh data.",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="Save results to FILE instead of printing to the terminal.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        help="File format when using --output. Default: csv",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="Records per API page. Default: 500",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between page requests. Default: 0.5",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max records to fetch (0 = no limit). Default: 0",
    )
    parser.add_argument(
        "--dump-fields",
        action="store_true",
        help=(
            "Probe the API and print every raw field it returns. "
            "Use this to discover what data is available for filtering."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed debug output.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    print_banner()

    if args.page_size <= 0:
        logging.error("--page-size must be greater than 0.")
        return 1
    if args.delay < 0:
        logging.error("--delay cannot be negative.")
        return 1

    session = create_session()

    if args.list_industries:
        print("\n  Available industry categories:\n")
        for i, label in enumerate(INDUSTRY_LABELS, 1):
            print(f"    {i:>2}. {label}")
        print(
            "\n  Use with: python scrape_skillbridge.py --industry 'Technology'\n"
            "  Partial matches work (e.g. --industry 'tech' or --industry 'health')\n"
        )
        return 0

    if args.dump_fields:
        return dump_raw_fields(session)

    # No flags → interactive guided mode
    if len(sys.argv) == 1:
        return run_interactive(
            session=session,
            page_size=args.page_size,
            delay=args.delay,
            refresh=args.refresh,
        )

    # Fetch (respects cache unless --refresh)
    try:
        organizations = get_organizations(
            session=session,
            page_size=args.page_size,
            delay=args.delay,
            limit=args.limit or None,
            refresh=args.refresh,
        )
    except requests.RequestException as exc:
        logging.error("HTTP request failed: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("Runtime error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logging.warning("Interrupted.")
        return 1

    if not organizations:
        logging.error("No records collected.")
        return 1

    results = filter_organizations(
        organizations,
        search=args.search,
        state=args.state,
        industry=args.industry,
    )

    if args.output:
        save_results(results, args.output, args.format)
    else:
        print_table(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
