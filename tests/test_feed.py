from pathlib import Path

import pytest
import yaml

from yandex_business_mcp import cli, server
from yandex_business_mcp.feed import build_feed, parse_feed
from yandex_business_mcp.rules import check_feed, validate_xsd
from yandex_business_mcp.workspace import Workspace, WorkspaceError, dump_branch, stringify

FIXTURE = Path(__file__).parent / "fixtures" / "yandex-example.xml"


def test_roundtrip_is_lossless():
    branches = parse_feed(FIXTURE)
    assert parse_feed(build_feed(branches)) == branches


def test_parsed_shape():
    a, b = parse_feed(FIXTURE)
    assert a["name"] == {"ru": "Якорь"}
    assert a["country"] == {"ru": "Россия", "en": "Russia"}
    assert a["rubric-id"] == ["184106414", "184106394"]
    assert a["phone"][1] == {"type": "phone", "number": "+7 (800) 200-23-45"}  # empty <info/> dropped
    assert a["photos"]["photo"][1] == {"url": "http://test.ru/yakor-anapa/19_b.jpg", "tags": ["EXTERIOR"]}
    assert {"feature": "boolean", "name": "internet", "value": "1"} in a["features"]
    assert b["scheduled-working-time"] == [
        {"holiday": "true", "items": [{"date": "01.01.2027"}, {"work": {"from": "10:00", "to": "16:00"}}]}
    ]


def test_yaml_roundtrip_survives():
    branches = parse_feed(FIXTURE)
    assert [stringify(yaml.safe_load(dump_branch(b))) for b in branches] == branches


def test_example_feed_is_valid():
    branches = parse_feed(FIXTURE)
    assert validate_xsd(build_feed(branches)) == []
    assert [i for i in check_feed(branches, {"184106414"}) if i.level == "error"] == []


def test_rules_catch_problems():
    bad = parse_feed(FIXTURE)[1] | {"company-id": "bad id", "url": "HTTP://X.RU"}
    bad["phone"] = [{"number": "1", "type": "mobile", "ext": "доб 5"}]
    bad["rubric-id"] = ["1", "2", "3", "4"]
    msgs = " | ".join(str(i) for i in check_feed([bad], {"184106414"}))
    for needle in ("company-id: only", "phone type", "ext must be digits", "1..3 rubric-id", "must start with http"):
        assert needle in msgs


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("YBIZ_WORKSPACE", str(tmp_path))
    assert cli.main(["init"]) == 0
    assert cli.main(["import", str(FIXTURE)]) == 0
    return Workspace()


def test_build_refuses_to_close_branches(ws):
    ws.branch_file("7707040070").unlink()
    with pytest.raises(WorkspaceError, match="CLOSE"):
        ws.build()
    assert cli.main(["build"]) == 1
    assert ws.build(["7707040070"]).branches == 1
    assert [b["company-id"] for b in parse_feed(ws.path("output"))] == ["770704034"]


def test_actualization_date_only_moves_on_change(ws):
    ws.build()
    feed = ws.path("output")
    feed.write_text(feed.read_text().replace("<actualization-date>", "<actualization-date>OLD-"))
    path = ws.branch_file("770704034")
    path.write_text(path.read_text().replace("ежедн. 10:00-21:00", "ежедн. 09:00-21:00"))
    assert ws.build().updated == ["770704034"]
    dates = {b["company-id"]: b["actualization-date"] for b in parse_feed(feed)}
    assert dates["7707040070"].startswith("OLD-")


def test_mcp_update_branch_validates(ws):
    ok = server.ybiz_update_branch("770704034", {"working-time": {"ru": "ежедн. 09:00-22:00"}})
    assert ok["saved"] == "770704034"
    assert ws.get_branch("770704034")["working-time"] == {"ru": "ежедн. 09:00-22:00"}

    bad = server.ybiz_update_branch("770704034", {"url": "ftp://nope"})
    assert "must start with http" in bad["error"]
    assert ws.get_branch("770704034")["url"] == "http://www.yakor-anapa.ru"

    assert "immutable" in server.ybiz_update_branch("770704034", {"company-id": "x1"})["error"]


def test_mcp_diff_and_list(ws):
    server.ybiz_update_branch("770704034", {"email": ["new@yakor-anapa.ru"]})
    assert server.ybiz_diff()["changed"] == {"770704034": ["email"]}
    assert {b["company-id"] for b in server.ybiz_list_branches()} == {"770704034", "7707040070"}


def test_percent_encoding_is_not_uppercase():
    b = parse_feed(FIXTURE)[1] | {"add-url": ["https://www.facebook.com/pages/%D0%A1%D0%9F-x/155"]}
    assert not any("lowercase" in str(i) for i in check_feed([b], {"184106414"}))
    b["add-url"] = ["https://Example.ru/"]
    assert any("lowercase" in str(i) for i in check_feed([b], {"184106414"}))
