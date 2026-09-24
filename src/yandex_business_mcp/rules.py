"""Feed checks: the official XSD plus the content rules from the Yandex Business docs.

https://yandex.ru/support/business-priority/ru/branches/branches-xml
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from yandex_business_mcp.feed import MULTILANG, PHONE_KEYS, comparable

XSD_PATH = Path(__file__).resolve().parent / "schema" / "partner-public.xsd"

COMPANY_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
PHONE_TYPES = {"phone", "fax", "phone-fax"}
PHOTO_TAGS = {"INTERIOR", "EXTERIOR", "LOGO", "FOOD", "ROUTE", "ENTER", "GOODS", "MENU", "DEVICES", "SERVICES", "ACCESSIBILITY"}
REQUIRED = ("company-id", "name", "address", "phone", "url", "working-time", "rubric-id")


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    company_id: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.company_id}: {self.message}"


def validate_xsd(xml: bytes) -> list[Issue]:
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    doc = etree.fromstring(xml)
    if schema.validate(doc):
        return []
    return [Issue("error", "<xsd>", f"line {e.line}: {e.message}") for e in schema.error_log]


def _strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif value is not None:
        yield str(value)


def _lang_values(branch: dict, key: str) -> list[str]:
    return list(_strings(branch.get(key, {})))


def check_branch(b: dict, chain_rubrics: set[str]) -> list[Issue]:
    cid = str(b.get("company-id", "<no id>"))
    out: list[Issue] = []
    err = lambda m: out.append(Issue("error", cid, m))  # noqa: E731
    warn = lambda m: out.append(Issue("warning", cid, m))  # noqa: E731

    for key in REQUIRED:
        if not b.get(key):
            err(f"missing required field `{key}`")

    if "company-id" in b and not COMPANY_ID.match(cid):
        err("company-id: only A-Z a-z 0-9 _ -, up to 80 chars, no spaces")

    for text in _strings({k: v for k, v in b.items() if k != "_raw"}):
        if CONTROL_CHARS.search(text):
            err(f"control character in {text[:40]!r}")
        if HTML_TAG.search(text):
            err(f"HTML markup is not allowed: {text[:40]!r}")
    if b.get("_raw"):
        warn(f"{len(b['_raw'])} element(s) unknown to ybiz kept verbatim; check them against the XSD")

    for key in MULTILANG:
        for value in _lang_values(b, key):
            if not value.strip():
                err(f"empty `{key}`")
    for name in _lang_values(b, "name"):
        if any(q in name for q in "\"«»“”"):
            warn(f"name should not contain quotes: {name!r}")
        if name.isupper() and len(name) > 3:
            warn(f"name should not be in caps: {name!r}")
    for short in _lang_values(b, "shortname"):
        if len(short) > 55:
            warn(f"shortname is {len(short)} chars (limit 55, 75 only by agreement)")
    for add in _lang_values(b, "address-add"):
        if any(c in add for c in "()\"«»"):
            warn(f"address-add should not contain brackets or quotes: {add!r}")

    for phone in b.get("phone", []):
        if not phone.get("number"):
            err("phone without number")
        if phone.get("type") not in PHONE_TYPES:
            err(f"phone type must be one of {sorted(PHONE_TYPES)}, got {phone.get('type')!r}")
        if phone.get("ext") and not str(phone["ext"]).isdigit():
            err(f"phone ext must be digits only: {phone['ext']!r}")
        if extra := set(phone) - set(PHONE_KEYS):
            err(f"unknown phone keys {sorted(extra)}")

    for key in ("url", "add-url", "info-page"):
        for url in _strings(b.get(key)):
            if not re.match(r"^https?://", url):
                err(f"{key} must start with http:// or https://: {url!r}")
            elif url != url.lower():
                warn(f"{key} should be lowercase: {url!r}")
            if "utm_" in url:
                warn(f"{key} has UTM tags (allowed only with active ads): {url!r}")

    rubrics = [str(r) for r in b.get("rubric-id", [])]
    if rubrics and not 1 <= len(rubrics) <= 3:
        err(f"1..3 rubric-id allowed, got {len(rubrics)}")
    if rubrics and chain_rubrics and not set(rubrics) & chain_rubrics:
        err(f"at least one rubric-id must match the chain rubric {sorted(chain_rubrics)}")

    if coords := b.get("coordinates"):
        try:
            lat, lon = float(coords["lat"]), float(coords["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                err(f"coordinates out of range: {coords}")
        except (KeyError, TypeError, ValueError):
            err(f"coordinates need numeric lat and lon: {coords}")

    for photo in b.get("photos", {}).get("photo", []):
        if not photo.get("url"):
            err("photo without url")
        for tag in photo.get("tags", []):
            if tag not in PHOTO_TAGS:
                err(f"unknown photo tag {tag!r}")
    return out


def check_feed(branches: list[dict], chain_rubrics: set[str] | None = None) -> list[Issue]:
    out: list[Issue] = []
    for b in branches:
        out += check_branch(b, chain_rubrics or set())

    seen: dict[str, int] = {}
    for b in branches:
        cid = str(b.get("company-id"))
        seen[cid] = seen.get(cid, 0) + 1
    out += [Issue("error", cid, f"company-id used {n} times") for cid, n in seen.items() if n > 1]

    countries = {c for b in branches for c in _lang_values(b, "country")}
    langs_per_country = {frozenset(b.get("country", {}).items()) for b in branches if b.get("country")}
    if len(langs_per_country) > 1:
        out.append(Issue("error", "<feed>", f"one feed = one country, found {sorted(countries)}"))

    by_place: dict[tuple, list[str]] = {}
    for b in branches:
        place = (tuple(_lang_values(b, "address")), tuple(sorted((b.get("coordinates") or {}).items())))
        by_place.setdefault(place, []).append(str(b.get("company-id")))
    for ids in by_place.values():
        if len(ids) > 1:
            out.append(Issue("warning", ",".join(ids), "same address and coordinates: possible duplicates"))
    return out


@dataclass
class Diff:
    added: list[str]
    removed: list[str]
    changed: dict[str, list[str]]


def diff_feeds(old: list[dict], new: list[dict]) -> Diff:
    old_by = {str(b["company-id"]): comparable(b) for b in old}
    new_by = {str(b["company-id"]): comparable(b) for b in new}
    changed = {}
    for cid in old_by.keys() & new_by.keys():
        o, n = old_by[cid], new_by[cid]
        keys = sorted(k for k in o.keys() | n.keys() if o.get(k) != n.get(k))
        if keys:
            changed[cid] = keys
    return Diff(
        added=sorted(new_by.keys() - old_by.keys()),
        removed=sorted(old_by.keys() - new_by.keys()),
        changed=dict(sorted(changed.items())),
    )
