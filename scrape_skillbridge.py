#!/usr/bin/env python3
"""
scrape_skillbridge.py — DoD SkillBridge Opportunity Finder

USAGE (just run this — setup is automatic):
    ./run.sh                                               # interactive guided mode
    python scrape_skillbridge.py --search "cyber"              # keyword search
    python scrape_skillbridge.py --industry "Technology"       # filter by industry
    python scrape_skillbridge.py --list-industries             # show industry list
    python scrape_skillbridge.py --refresh                     # force fresh download
    python scrape_skillbridge.py -o results.csv                # save all to file
    python scrape_skillbridge.py --industry "Healthcare" -o out.csv
    python scrape_skillbridge.py --format json -o out.json

    # Detailed opportunities with direct employer contact info:
    python scrape_skillbridge.py --opportunities               # all opportunities
    python scrape_skillbridge.py --opportunities --state TX    # filter by state
    python scrape_skillbridge.py --opportunities --virtual     # remote/virtual only
    python scrape_skillbridge.py --opportunities --branch Army # filter by branch
    python scrape_skillbridge.py --opportunities --moc 25B     # filter by MOS/MOC
    python scrape_skillbridge.py --opportunities --search "cyber" -o ops.csv
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

# ── API endpoints ─────────────────────────────────────────────────────────────

API_URL = "https://api.skillbridge.mil/Organizations/Authorized"
LOCATION_API_URL = "https://api.skillbridge.mil/Location/Lookup"

LOCATION_COL_MATRIX = (
    "1-Organization,2-Program,3-Mission,4-City,5-State,6-Zip,"
    "7-Duration,8-EmployerPoc,9-EmployerPocEmail,10-DeliveryMethodId,"
    "11-Branch,12-Installation,13-LocationStates,14-TargetMOCs,"
    "15-OtherEligibilityFactors,16-Other,17-JobDescription,18-Summary,19-Industries"
)

CACHE_FILE = Path(__file__).parent / "skillbridge_cache.json"
LOCATION_CACHE_FILE = Path(__file__).parent / "skillbridge_locations_cache.json"
CACHE_MAX_AGE_DAYS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.skillbridge.mil",
    "Referer": "https://www.skillbridge.mil/",
}

CA_BUNDLE = certifi.where()
CSV_FIELDS = ["id", "name", "industry", "website", "mou_expiration"]

DELIVERY_METHODS: dict[int, str] = {1: "In-Person", 2: "Virtual", 3: "Hybrid"}

LOCATION_CSV_FIELDS = [
    "id", "organization", "program", "city", "state", "zip_code",
    "duration", "employer_poc", "employer_poc_email", "delivery_method",
    "branches", "installation", "location_states", "target_mocs",
    "other_eligibility", "industries", "industry", "job_description", "summary",
]

# ── Industry auto-tagging ─────────────────────────────────────────────────────

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

_COMPILED_MAP: list[tuple[str, list[re.Pattern[str]]]] = [
    (label, [re.compile(p, re.IGNORECASE) for p in patterns])
    for label, patterns in INDUSTRY_MAP
]


def tag_industry(text: str) -> str:
    """Return the best-matching industry label for a text string."""
    for label, patterns in _COMPILED_MAP:
        if any(p.search(text) for p in patterns):
            return label
    return "Other"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class Organization:
    id: str | int | None
    name: str
    industry: str
    website: str | None
    mou_expiration: str | None


@dataclass
class Opportunity:
    """Rich opportunity record from the Location/Lookup endpoint."""
    id: int | None
    organization: str
    program: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    duration: str | None
    employer_poc: str | None
    employer_poc_email: str | None
    delivery_method: str          # "In-Person" | "Virtual" | "Hybrid"
    branches: list[str]
    installation: str | None
    location_states: str | None   # e.g. "TX, OK" or "Nationwide"
    target_mocs: str | None
    other_eligibility: str | None
    job_description: str | None
    summary: str | None
    industries: str | None
    industry: str                 # auto-tagged category


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


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Organizations API ─────────────────────────────────────────────────────────

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
    name = clean_string(raw.get("name")) or ""
    return Organization(
        id=raw.get("id"),
        name=name,
        industry=tag_industry(name),
        website=clean_string(raw.get("url")),
        mou_expiration=clean_date(raw.get("mexd")),
    )


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


# ── Location/Lookup API ───────────────────────────────────────────────────────

FILTERS_API_URL = "https://api.skillbridge.mil/Location/Filters"


def fetch_location_filters(session: requests.Session, timeout: int = 30) -> dict[str, Any]:
    """Return the /Location/Filters payload (states list, branch list, totalPositions, etc.)."""
    response = session.get(FILTERS_API_URL, timeout=timeout)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Location/Filters response was not valid JSON.") from exc


def build_location_params(
    start: int,
    length: int,
    state: str | None = None,
    delivery_method: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "draw": 1,
        "order[0][column]": 1,
        "order[0][dir]": "asc",
        "start": start,
        "length": length,
        "search[value]": "",
        "search[regex]": "false",
        "colMatrix": LOCATION_COL_MATRIX,
        "mobile": "false",
    }
    if state is not None:
        params["state"] = state
    if delivery_method is not None:
        params["deliveryMethod"] = delivery_method
    return params


def fetch_location_page(
    session: requests.Session,
    start: int,
    length: int,
    state: str | None = None,
    delivery_method: int | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    response = session.get(
        LOCATION_API_URL,
        params=build_location_params(start, length, state, delivery_method),
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Location API response was not valid JSON.") from exc


def normalize_opportunity(raw: dict[str, Any]) -> Opportunity:
    org_text = clean_string(raw.get("organization")) or ""
    industries_text = clean_string(raw.get("industries")) or ""
    tag_text = industries_text or org_text

    delivery_id = raw.get("deliveryMethodId") or 0
    delivery = DELIVERY_METHODS.get(int(delivery_id), f"Method {delivery_id}")

    branches = raw.get("branches") or []
    if isinstance(branches, str):
        branches = [b.strip() for b in branches.split(",") if b.strip()]

    return Opportunity(
        id=raw.get("id"),
        organization=org_text,
        program=clean_string(raw.get("program")),
        city=clean_string(raw.get("city")),
        state=clean_string(raw.get("state")),
        zip_code=clean_string(raw.get("zip")),
        duration=clean_string(raw.get("duration")),
        employer_poc=clean_string(raw.get("employerPoc")),
        employer_poc_email=clean_string(raw.get("employerPocEmail")),
        delivery_method=delivery,
        branches=branches,
        installation=clean_string(raw.get("installation")),
        location_states=clean_string(raw.get("locationStates")),
        target_mocs=clean_string(raw.get("targetMOCs")),
        other_eligibility=clean_string(raw.get("otherEligibilityFactors")),
        job_description=clean_string(raw.get("jobDescription")),
        summary=clean_string(raw.get("summary")),
        industries=industries_text or None,
        industry=tag_industry(tag_text),
    )


def _fetch_all_for_state(
    session: requests.Session,
    state: str,
    page_size: int,
    delay: float,
) -> list[dict[str, Any]]:
    """Fetch every raw record for a single state, paginating until exhausted."""
    first = fetch_location_page(session=session, start=0, length=page_size, state=state)
    total = int(first.get("recordsTotal", 0))
    rows: list[dict[str, Any]] = list(first.get("data", []))

    start = page_size
    while start < total:
        if delay > 0:
            time.sleep(delay)
        page = fetch_location_page(session=session, start=start, length=page_size, state=state)
        batch = page.get("data", [])
        if not batch:
            break
        rows.extend(batch)
        start += page_size

    return rows


def iter_opportunities(
    session: requests.Session,
    page_size: int,
    delay: float,
) -> Generator[Opportunity, None, None]:
    """
    The Location/Lookup API caps at ~150 records without a state filter.
    To get the full ~13,000+ dataset we sweep every state from /Location/Filters,
    then deduplicate by record ID so overlapping 'Nationwide' entries aren't doubled.
    """
    logging.info("Fetching state list from /Location/Filters …")
    try:
        filter_data = fetch_location_filters(session)
    except Exception as exc:
        logging.warning("Could not fetch filters (%s) — falling back to default query.", exc)
        filter_data = {}

    states: list[str] = filter_data.get("states") or []
    total_positions = filter_data.get("meta", {}).get("totalPositions", "?")

    if states:
        logging.info(
            "Sweeping %d states to collect up to %s total positions …",
            len(states),
            total_positions,
        )
    else:
        logging.warning("No state list available — fetching without state filter (limited to ~150).")
        states = [None]  # type: ignore[list-item]

    seen_ids: set[Any] = set()

    for i, state in enumerate(states, 1):
        label = state or "<no filter>"
        logging.info("[%d/%d] Fetching state: %s", i, len(states), label)

        try:
            raw_rows = _fetch_all_for_state(
                session=session, state=state, page_size=page_size, delay=delay
            )
        except Exception as exc:
            logging.warning("Skipping state %s: %s", label, exc)
            continue

        new_count = 0
        for raw in raw_rows:
            rec_id = raw.get("id")
            if rec_id in seen_ids:
                continue
            seen_ids.add(rec_id)
            yield normalize_opportunity(raw)
            new_count += 1

        logging.debug("  %s: %d records (%d new unique)", label, len(raw_rows), new_count)

        if delay > 0:
            time.sleep(delay)

    logging.info("Total unique opportunities collected: %d", len(seen_ids))


# ── Field discovery ───────────────────────────────────────────────────────────

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
    """Probe the Organizations API with several colMatrix values and report every field."""
    print("\n  Probing the SkillBridge Organizations API for available data fields …\n")

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

    print(f"  {'─' * 60}")
    print(f"  All unique fields across all probes ({len(seen_keys)} total):\n")

    merged_sample: dict[str, Any] = {}
    for v in variants:
        for k, val in v["sample"].items():
            if k not in merged_sample or merged_sample[k] in (None, "", []):
                merged_sample[k] = val

    for key in sorted(seen_keys):
        raw_val = merged_sample.get(key, "<absent>")
        display = repr(str(raw_val)[:70])
        print(f"    {key:<22} → {display}")

    dump_path = Path("skillbridge_raw_dump.json")
    with dump_path.open("w", encoding="utf-8") as f:
        json.dump(
            {"all_fields": sorted(seen_keys), "variants": variants},
            f,
            indent=2,
        )

    print(f"\n  Full raw response saved to: {dump_path}\n")
    return 0


def dump_location_fields(session: requests.Session) -> int:
    """Probe the Location/Lookup API and report every field returned."""
    print("\n  Probing the SkillBridge Location/Lookup API for available data fields …\n")

    try:
        page = fetch_location_page(session, start=0, length=3)
        records: list[dict[str, Any]] = page.get("data", [])
    except Exception as exc:
        print(f"  Error: {exc}\n")
        return 1

    if not records:
        print("  No records returned.\n")
        return 1

    all_keys = {k for rec in records for k in rec}
    print(f"  Total: {page.get('recordsTotal', '?')} opportunities\n")
    print(f"  Fields returned ({len(all_keys)}):\n")

    sample = records[0]
    for key in sorted(all_keys):
        val = sample.get(key, "<absent>")
        display = repr(str(val)[:80])
        print(f"    {key:<28} → {display}")

    dump_path = Path("skillbridge_location_dump.json")
    with dump_path.open("w", encoding="utf-8") as f:
        json.dump({"recordsTotal": page.get("recordsTotal"), "sample": records}, f, indent=2)

    print(f"\n  Full sample saved to: {dump_path}\n")
    return 0


# ── Cache — Organizations ─────────────────────────────────────────────────────

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
                industry=item.get("industry") or tag_industry(name),
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

    if organizations and limit is None:
        save_cache(organizations)

    return organizations


# ── Cache — Opportunities ─────────────────────────────────────────────────────

def load_location_cache() -> list[Opportunity] | None:
    if not LOCATION_CACHE_FILE.exists():
        return None
    try:
        with LOCATION_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        fetched_str = data["fetched_at"].replace("Z", "+00:00")
        fetched_at = datetime.fromisoformat(fetched_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - fetched_at).days
        if age_days > CACHE_MAX_AGE_DAYS:
            logging.info("Location cache is %d days old — will refresh.", age_days)
            return None

        opps = []
        for item in data["opportunities"]:
            opps.append(Opportunity(
                id=item.get("id"),
                organization=item.get("organization", ""),
                program=item.get("program"),
                city=item.get("city"),
                state=item.get("state"),
                zip_code=item.get("zip_code"),
                duration=item.get("duration"),
                employer_poc=item.get("employer_poc"),
                employer_poc_email=item.get("employer_poc_email"),
                delivery_method=item.get("delivery_method", ""),
                branches=item.get("branches") or [],
                installation=item.get("installation"),
                location_states=item.get("location_states"),
                target_mocs=item.get("target_mocs"),
                other_eligibility=item.get("other_eligibility"),
                job_description=item.get("job_description"),
                summary=item.get("summary"),
                industries=item.get("industries"),
                industry=item.get("industry", "Other"),
            ))
        logging.info(
            "Loaded %d opportunities from local cache (%d day(s) old).",
            len(opps),
            age_days,
        )
        return opps

    except Exception as exc:
        logging.debug("Location cache unreadable (%s) — fetching fresh data.", exc)
        return None


def save_location_cache(opportunities: list[Opportunity]) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "opportunities": [asdict(opp) for opp in opportunities],
    }
    try:
        with LOCATION_CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logging.debug("Saved %d opportunities to location cache.", len(opportunities))
    except Exception as exc:
        logging.warning("Could not save location cache: %s", exc)


def get_opportunities(
    session: requests.Session,
    page_size: int,
    delay: float,
    refresh: bool = False,
) -> list[Opportunity]:
    if not refresh:
        cached = load_location_cache()
        if cached is not None:
            return cached

    opportunities = list(
        iter_opportunities(session=session, page_size=page_size, delay=delay)
    )

    if opportunities:
        save_location_cache(opportunities)

    return opportunities


# ── Filters ───────────────────────────────────────────────────────────────────

def filter_organizations(
    organizations: list[Organization],
    search: str | None = None,
    industry: str | None = None,
) -> list[Organization]:
    results = organizations

    if search:
        kw = search.lower()
        results = [o for o in results if kw in o.name.lower()]

    if industry:
        target = industry.lower().strip()
        results = [o for o in results if target in o.industry.lower()]

    return results


def filter_opportunities(
    opportunities: list[Opportunity],
    search: str | None = None,
    state: str | None = None,
    city: str | None = None,
    branch: str | None = None,
    moc: str | None = None,
    virtual_only: bool = False,
    industry: str | None = None,
) -> list[Opportunity]:
    results = opportunities

    if search:
        kw = search.lower()
        results = [
            o for o in results
            if kw in o.organization.lower()
            or kw in (o.program or "").lower()
            or kw in (o.job_description or "").lower()
            or kw in (o.summary or "").lower()
            or kw in (o.industries or "").lower()
        ]

    if state:
        st = state.upper().strip()
        results = [
            o for o in results
            if (o.state or "").upper() == st
            or st in (o.location_states or "").upper()
            or (o.location_states or "").upper() == "NATIONWIDE"
        ]

    if city:
        ct = city.lower().strip()
        results = [
            o for o in results
            if ct in (o.city or "").lower()
        ]

    if branch:
        br = branch.lower().strip()
        results = [
            o for o in results
            if any(br in b.lower() for b in o.branches)
        ]

    if moc:
        m = moc.upper().strip()
        results = [
            o for o in results
            if m in (o.target_mocs or "").upper()
        ]

    if virtual_only:
        results = [o for o in results if o.delivery_method == "Virtual"]

    if industry:
        target = industry.lower().strip()
        results = [o for o in results if target in o.industry.lower()]

    return results


# ── Terminal tables ───────────────────────────────────────────────────────────

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


def print_opportunity_table(opportunities: list[Opportunity]) -> None:
    if not opportunities:
        print("\n  No matching opportunities found.\n")
        return

    def truncate(s: str, width: int) -> str:
        return (s[: width - 1] + "…") if len(s) > width else s

    org_w   = min(38, max(len(o.organization) for o in opportunities))
    loc_w   = 14
    dur_w   = 18
    email_w = min(34, max(len(o.employer_poc_email or "") for o in opportunities))

    def make_row(
        num: Any, org: str, location: str, method: str, duration: str, email: str
    ) -> str:
        return (
            f"  {str(num):>4} | "
            f"{org:<{org_w}} | "
            f"{location:<{loc_w}} | "
            f"{method:<10} | "
            f"{duration:<{dur_w}} | "
            f"{email}"
        )

    header = make_row(
        "#", "Organization", "Location", "Mode", "Duration", "Employer Email"
    )
    sep = "  " + "-" * (len(header) - 2)

    print(f"\n  Found {len(opportunities)} opportunity/ies:\n")
    print(header)
    print(sep)

    for i, opp in enumerate(opportunities, 1):
        if opp.state == "Virtual":
            location = "Virtual"
        elif opp.city and opp.state:
            location = f"{opp.city[:8]}, {opp.state}"
        elif opp.state:
            location = opp.state
        else:
            location = opp.location_states or "?"

        print(make_row(
            i,
            truncate(opp.organization, org_w),
            truncate(location, loc_w),
            truncate(opp.delivery_method, 10),
            truncate(opp.duration or "N/A", dur_w),
            truncate(opp.employer_poc_email or "N/A", email_w),
        ))

    print()

    # Show target MOCs summary if any exist
    with_mocs = [o for o in opportunities if o.target_mocs]
    if with_mocs:
        print(f"  {len(with_mocs)} of {len(opportunities)} have target MOS/MOC info.")
        print("  Use --format json or -o file.csv to see full job descriptions and MOC details.\n")


def print_opportunity_detail(opp: Opportunity, num: int) -> None:
    """Print a full detail card for one opportunity."""
    print(f"\n  {'═' * 60}")
    print(f"  #{num}: {opp.organization}")
    if opp.program and opp.program != opp.organization:
        print(f"  Program: {opp.program}")
    print(f"  {'─' * 60}")
    if opp.city or opp.state:
        loc = f"{opp.city}, {opp.state}" if opp.city and opp.state else (opp.city or opp.state)
        print(f"  Location:    {loc} ({opp.delivery_method})")
    if opp.location_states:
        print(f"  Open to:     {opp.location_states}")
    if opp.duration:
        print(f"  Duration:    {opp.duration}")
    if opp.branches:
        print(f"  Branches:    {', '.join(opp.branches)}")
    if opp.employer_poc:
        print(f"  Contact:     {opp.employer_poc}")
    if opp.employer_poc_email:
        print(f"  Email:       {opp.employer_poc_email}")
    if opp.target_mocs:
        print(f"  Target MOCs: {opp.target_mocs}")
    if opp.industries:
        print(f"  Industries:  {opp.industries}")
    if opp.summary:
        print(f"\n  Summary:\n  {opp.summary[:400]}{'…' if len(opp.summary or '') > 400 else ''}")
    if opp.job_description:
        print(f"\n  Job Description:\n  {opp.job_description[:400]}{'…' if len(opp.job_description or '') > 400 else ''}")
    if opp.other_eligibility:
        print(f"\n  Eligibility Notes:\n  {opp.other_eligibility}")
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


def write_opportunity_csv(output_path: Path, opportunities: Iterable[Opportunity]) -> int:
    count = 0
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LOCATION_CSV_FIELDS)
        writer.writeheader()
        for opp in opportunities:
            row = asdict(opp)
            row["branches"] = ", ".join(opp.branches)
            writer.writerow({k: row.get(k, "") for k in LOCATION_CSV_FIELDS})
            count += 1
    return count


def write_opportunity_json(output_path: Path, opportunities: Iterable[Opportunity]) -> int:
    data = [asdict(opp) for opp in opportunities]
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return len(data)


DOWNLOADS_DIR = Path.home() / "Downloads"


def resolve_output_path(output: str, fmt: str) -> Path:
    """If output is a bare filename (no directory), save it to ~/Downloads."""
    p = Path(output)
    if not p.parent.name or p.parent == Path("."):
        p = DOWNLOADS_DIR / p.name
    if fmt != "json" and p.suffix.lower() not in (".csv", ".json"):
        p = p.with_suffix(".csv")
    return p


def save_results(
    results: list[Organization] | list[Opportunity],
    output: str,
    fmt: str,
) -> None:
    out_path = resolve_output_path(output, fmt)
    is_json = fmt == "json" or out_path.suffix.lower() == ".json"

    if results and isinstance(results[0], Opportunity):
        count = write_opportunity_json(out_path, results) if is_json else write_opportunity_csv(out_path, results)  # type: ignore[arg-type]
    else:
        count = write_json(out_path, results) if is_json else write_csv(out_path, results)  # type: ignore[arg-type]

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
    print("  What would you like to do?\n")
    print("    1. Browse all authorized organizations  (search by name / industry)")
    print("    2. Find detailed opportunities          (contact info, location, MOC targeting)")
    print()

    try:
        mode_choice = input("  Choose 1 or 2 (or Enter for default '2'): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 0

    if mode_choice == "1":
        return _interactive_orgs(session, page_size, delay, refresh)
    else:
        return _interactive_opportunities(session, page_size, delay, refresh)


def _interactive_orgs(
    session: requests.Session,
    page_size: int,
    delay: float,
    refresh: bool,
) -> int:
    print()
    print("  Industry categories:\n")
    for i, label in enumerate(INDUSTRY_LABELS, 1):
        print(f"    {i:>2}. {label}")
    print()

    try:
        ind_choice = input("  Pick an industry number (or press Enter to skip): ").strip()
        industry: str | None = None
        if ind_choice.isdigit():
            idx = int(ind_choice) - 1
            if 0 <= idx < len(INDUSTRY_LABELS):
                industry = INDUSTRY_LABELS[idx]
            else:
                print("  Invalid number — skipping industry filter.")
        elif ind_choice:
            industry = ind_choice

        search = input("  Keyword in company name (or Enter to skip): ").strip() or None
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 0

    print()
    print("  Fetching data (this may take a moment the first time) …")

    try:
        organizations = get_organizations(
            session=session, page_size=page_size, delay=delay, refresh=refresh,
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

    results = filter_organizations(organizations, search=search, industry=industry)
    print_table(results)

    if results:
        try:
            dest = input(
                "  Save to file? (e.g. results.csv — saved to ~/Downloads, or Enter to skip): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            dest = ""

        if dest:
            fmt = "json" if dest.endswith(".json") else "csv"
            save_results(results, dest, fmt)  # type: ignore[arg-type]

    return 0


def _interactive_opportunities(
    session: requests.Session,
    page_size: int,
    delay: float,
    refresh: bool,
) -> int:
    print()
    print("  Opportunities include: employer contact email, location, duration,")
    print("  target MOC/MOS, eligible branches, and job descriptions.\n")

    try:
        state_in = input(
            "  Filter by state (2-letter code, e.g. CA) or 'virtual' for remote only\n"
            "  (or Enter to show all): "
        ).strip()

        city_in = input(
            "  Filter by city (e.g. San Diego) or Enter to skip: "
        ).strip() or None

        branch_in = input(
            "  Filter by branch (Army / Navy / Air Force / Marine Corps / Coast Guard / Space Force)\n"
            "  (or Enter to show all): "
        ).strip() or None

        moc_in = input(
            "  Filter by MOS/MOC (e.g. 25B, 68W, 11B)\n"
            "  (or Enter to show all): "
        ).strip() or None

        search_in = input(
            "  Keyword search in job title/description (or Enter to skip): "
        ).strip() or None

    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 0

    virtual_only = state_in.lower() in ("virtual", "v", "remote")
    state_filter = None if virtual_only or not state_in else state_in.upper()

    print()
    print("  Fetching opportunities …")

    try:
        opportunities = get_opportunities(
            session=session, page_size=page_size, delay=delay, refresh=refresh,
        )
    except requests.RequestException as exc:
        logging.error("HTTP request failed: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("Runtime error: %s", exc)
        return 1

    if not opportunities:
        logging.error("No opportunities retrieved.")
        return 1

    results = filter_opportunities(
        opportunities,
        search=search_in,
        state=state_filter,
        city=city_in,
        branch=branch_in,
        moc=moc_in,
        virtual_only=virtual_only,
    )

    print_opportunity_table(results)

    if results:
        try:
            detail_in = input(
                "  Enter a row number to see full details (or Enter to skip): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            detail_in = ""

        if detail_in.isdigit():
            idx = int(detail_in) - 1
            if 0 <= idx < len(results):
                print_opportunity_detail(results[idx], idx + 1)

        try:
            dest = input(
                "  Save to file? (e.g. opportunities.csv — saved to ~/Downloads, or Enter to skip): "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            dest = ""

        if dest:
            fmt = "json" if dest.endswith(".json") else "csv"
            save_results(results, dest, fmt)  # type: ignore[arg-type]

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find DoD SkillBridge authorized organizations and opportunities.",
        epilog=(
            "Run with no arguments for interactive guided mode.\n\n"
            "Organization search (full catalog):\n"
            "  python scrape_skillbridge.py --search 'cyber'\n"
            "  python scrape_skillbridge.py --industry 'Technology'\n"
            "  python scrape_skillbridge.py --list-industries\n\n"
            "Opportunity search (with contact info, location, MOC targeting):\n"
            "  python scrape_skillbridge.py --opportunities\n"
            "  python scrape_skillbridge.py --opportunities --state CA\n"
            "  python scrape_skillbridge.py --opportunities --state CA --city 'San Diego'\n"
            "  python scrape_skillbridge.py --opportunities --virtual\n"
            "  python scrape_skillbridge.py --opportunities --branch Army\n"
            "  python scrape_skillbridge.py --opportunities --moc 25B\n"
            "  python scrape_skillbridge.py --opportunities -o ops.csv\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Organization search flags
    parser.add_argument("--search", metavar="KEYWORD",
        help="Filter by keyword in organization/program name or job description.")
    parser.add_argument("--industry", metavar="INDUSTRY",
        help="Filter by industry category. Use --list-industries to see options.")
    parser.add_argument("--list-industries", action="store_true",
        help="Print all available industry categories and exit.")

    # Opportunity-mode flags
    parser.add_argument("--opportunities", action="store_true",
        help="Search detailed opportunities (contact info, location, MOC targeting).")
    parser.add_argument("--state", metavar="STATE",
        help="[--opportunities] Filter by 2-letter state code (e.g. CA). Also matches Nationwide.")
    parser.add_argument("--city", metavar="CITY",
        help="[--opportunities] Filter by city name (e.g. 'San Diego'). Partial match.")
    parser.add_argument("--virtual", action="store_true",
        help="[--opportunities] Show only remote/virtual opportunities.")
    parser.add_argument("--branch", metavar="BRANCH",
        help="[--opportunities] Filter by eligible branch (e.g. Army, Navy, Air Force).")
    parser.add_argument("--moc", metavar="MOC",
        help="[--opportunities] Filter by MOS/MOC code (e.g. 25B, 68W, 11B).")

    # Shared flags
    parser.add_argument("--refresh", action="store_true",
        help="Ignore the local cache and download fresh data.")
    parser.add_argument("-o", "--output", metavar="FILE",
        help="Save results to FILE instead of printing to the terminal.")
    parser.add_argument("--format", choices=["csv", "json"], default="csv",
        help="File format when using --output. Default: csv")
    parser.add_argument("--page-size", type=int, default=500,
        help="Records per API page. Default: 500")
    parser.add_argument("--delay", type=float, default=0.5,
        help="Seconds between page requests. Default: 0.5")
    parser.add_argument("--limit", type=int, default=0,
        help="Max org records to fetch (0 = no limit). Default: 0")
    parser.add_argument("--dump-fields", action="store_true",
        help="Probe the Organizations API and print every raw field it returns.")
    parser.add_argument("--dump-location-fields", action="store_true",
        help="Probe the Location/Lookup API and print every raw field it returns.")
    parser.add_argument("--verbose", action="store_true",
        help="Show detailed debug output.")

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

    if args.dump_location_fields:
        return dump_location_fields(session)

    # No flags → interactive guided mode
    if len(sys.argv) == 1:
        return run_interactive(
            session=session,
            page_size=args.page_size,
            delay=args.delay,
            refresh=args.refresh,
        )

    # ── Opportunity mode ──────────────────────────────────────────────────────
    if args.opportunities:
        try:
            opportunities = get_opportunities(
                session=session,
                page_size=args.page_size,
                delay=args.delay,
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

        if not opportunities:
            logging.error("No opportunities retrieved.")
            return 1

        results_opp = filter_opportunities(
            opportunities,
            search=args.search,
            state=args.state,
            city=args.city,
            branch=args.branch,
            moc=args.moc,
            virtual_only=args.virtual,
            industry=args.industry,
        )

        if args.output:
            save_results(results_opp, args.output, args.format)  # type: ignore[arg-type]
        else:
            print_opportunity_table(results_opp)

        return 0

    # ── Organization mode (default) ───────────────────────────────────────────
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

    results_org = filter_organizations(
        organizations,
        search=args.search,
        industry=args.industry,
    )

    if args.output:
        save_results(results_org, args.output, args.format)  # type: ignore[arg-type]
    else:
        print_table(results_org)

    return 0


if __name__ == "__main__":
    sys.exit(main())
