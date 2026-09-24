"""Lossless conversion between the Yandex Business XML feed and branch dicts.

A branch dict is what lives in data/branches/<company-id>.yaml:

    company-id: moscow_15
    name: {ru: Ромашка}                 # multilingual: lang -> str | [str]
    phone: [{number: ..., type: phone}]
    rubric-id: ["184106414"]            # repeatable plain field: always a list
    url: https://example.ru             # plain field: str (list if repeated)
    features: [{feature: boolean, name: internet, value: "1"}]

Elements the schema does not know are kept verbatim under `_raw`.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

NO_LANG = "_"

MULTILANG = ("name", "shortname", "name-other", "country", "address", "address-add", "working-time")
PLAIN_SCALAR = ("company-id", "post-index", "url", "info-page", "chain-id", "inn", "ogrn", "actualization-date")
PLAIN_LIST = ("email", "add-url", "rubric-id")
PHONE_KEYS = ("number", "ext", "info", "type")

# Canonical output order; the schema is an unordered <xs:choice>, this is for readable diffs.
ORDER = (
    "company-id", "name", "shortname", "name-other", "country", "post-index", "address",
    "address-add", "coordinates", "phone", "email", "url", "add-url", "info-page",
    "working-time", "scheduled-working-time", "rubric-id", "chain-id", "inn", "ogrn",
    "photos", "features", "actualization-date", "_raw",
)


def _text(el: etree._Element) -> str:
    return (el.text or "").strip()


def _put_multi(branch: dict, tag: str, lang: str, value: str) -> None:
    langs = branch.setdefault(tag, {})
    if lang in langs:
        prev = langs[lang]
        langs[lang] = (prev if isinstance(prev, list) else [prev]) + [value]
    else:
        langs[lang] = value


def _put_plain(branch: dict, tag: str, value: str) -> None:
    if tag in PLAIN_LIST:
        branch.setdefault(tag, []).append(value)
    elif tag in branch:
        prev = branch[tag]
        branch[tag] = (prev if isinstance(prev, list) else [prev]) + [value]
    else:
        branch[tag] = value


def company_to_dict(el: etree._Element) -> dict:
    branch: dict = {}
    for child in el:
        if not isinstance(child.tag, str):  # comments / processing instructions
            continue
        tag = child.tag
        if tag in MULTILANG:
            if value := _text(child):
                _put_multi(branch, tag, child.get("lang", NO_LANG), value)
        elif tag in PLAIN_SCALAR or tag in PLAIN_LIST:
            if value := _text(child):
                _put_plain(branch, tag, value)
        elif tag == "phone":
            phone = {sub.tag: _text(sub) for sub in child if isinstance(sub.tag, str) and _text(sub)}
            if phone:
                branch.setdefault("phone", []).append(phone)
        elif tag == "coordinates":
            branch["coordinates"] = {sub.tag: _text(sub) for sub in child if isinstance(sub.tag, str)}
        elif tag == "scheduled-working-time":
            entry: dict = {}
            if child.get("holiday") is not None:
                entry["holiday"] = child.get("holiday")
            entry["items"] = [
                {sub.tag: _text(sub)} if sub.tag == "date" else {sub.tag: dict(sub.attrib)}
                for sub in child
                if isinstance(sub.tag, str)
            ]
            branch.setdefault("scheduled-working-time", []).append(entry)
        elif tag == "photos":
            photos: dict = {}
            if child.get("gallery-url"):
                photos["gallery-url"] = child.get("gallery-url")
            photos["photo"] = []
            for ph in child.iter("photo"):
                photo = dict(ph.attrib)
                tags = [_text(t) for t in ph.iter("tag") if _text(t)]
                if tags:
                    photo["tags"] = tags
                photos["photo"].append(photo)
            branch["photos"] = photos
        elif tag.startswith("feature-"):
            branch.setdefault("features", []).append({"feature": tag.removeprefix("feature-"), **child.attrib})
        else:
            branch.setdefault("_raw", []).append(etree.tostring(child, encoding="unicode").strip())
    return branch


def parse_feed(source: str | Path | bytes) -> list[dict]:
    parser = etree.XMLParser(remove_blank_text=True)
    if isinstance(source, bytes):
        root = etree.fromstring(source, parser)
    else:
        root = etree.parse(str(source), parser).getroot()
    if root.tag != "companies":
        raise ValueError(f"root element must be <companies>, got <{root.tag}>")
    return [company_to_dict(c) for c in root.iter("company")]


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, tag, {k: str(v) for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def dict_to_company(branch: dict, parent: etree._Element) -> etree._Element:
    unknown = set(branch) - set(ORDER)
    if unknown:
        raise ValueError(f"{branch.get('company-id')}: unknown keys {sorted(unknown)}")
    company = _sub(parent, "company")
    for key in ORDER:
        if key not in branch:
            continue
        value = branch[key]
        if key in MULTILANG:
            for lang, texts in value.items():
                for text in _as_list(texts):
                    _sub(company, key, text, **({} if lang == NO_LANG else {"lang": lang}))
        elif key in PLAIN_SCALAR or key in PLAIN_LIST:
            for text in _as_list(value):
                _sub(company, key, text)
        elif key == "phone":
            for phone in value:
                if extra := set(phone) - set(PHONE_KEYS):
                    raise ValueError(f"{branch.get('company-id')}: unknown phone keys {sorted(extra)}")
                el = _sub(company, "phone")
                for sub in PHONE_KEYS:
                    if phone.get(sub) not in (None, ""):
                        _sub(el, sub, phone[sub])
        elif key == "coordinates":
            el = _sub(company, "coordinates")
            _sub(el, "lon", value["lon"])
            _sub(el, "lat", value["lat"])
        elif key == "scheduled-working-time":
            for entry in value:
                el = _sub(company, "scheduled-working-time", holiday=entry.get("holiday"))
                for item in entry["items"]:
                    (tag, payload), = item.items()
                    if tag == "date":
                        _sub(el, "date", payload)
                    else:
                        _sub(el, tag, **payload)
        elif key == "photos":
            el = _sub(company, "photos", **{"gallery-url": value.get("gallery-url")})
            for photo in value["photo"]:
                attrs = {k: v for k, v in photo.items() if k != "tags"}
                ph = _sub(el, "photo", **attrs)
                for tag in photo.get("tags", []):
                    _sub(ph, "tag", tag)
        elif key == "features":
            for feat in value:
                attrs = {k: v for k, v in feat.items() if k != "feature"}
                _sub(company, f"feature-{feat['feature']}", **attrs)
        elif key == "_raw":
            for raw in value:
                company.append(etree.fromstring(raw))
    return company


def build_feed(branches: list[dict]) -> bytes:
    root = etree.Element("companies")
    for branch in branches:
        dict_to_company(branch, root)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True).replace(
        b"<?xml version='1.0' encoding='UTF-8'?>", b'<?xml version="1.0" encoding="UTF-8"?>', 1
    )


def comparable(branch: dict) -> dict:
    """Branch without bookkeeping fields, for change detection."""
    return {k: v for k, v in branch.items() if k != "actualization-date"}
