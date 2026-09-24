"""A workspace is a (private) directory holding one chain's data:

    ybiz.yaml                          chain config
    data/branches/<company-id>.yaml    source of truth, one file per branch
    data/baseline/yandex-export.xml    last export from the Yandex Business cabinet
    feed/feed.xml                      built feed, published at a stable URL

The tool code knows nothing about a particular client; everything client-specific lives here.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from yandex_business_mcp.feed import ORDER, build_feed, comparable, parse_feed
from yandex_business_mcp.rules import Issue, check_feed, diff_feeds, validate_xsd

DEFAULTS = {
    "chain_id": None,
    "chain_rubric_ids": [],
    "branches_dir": "data/branches",
    "baseline": "data/baseline/yandex-export.xml",
    "output": "feed/feed.xml",
}

CONFIG_TEMPLATE = """\
# Сеть в Яндекс Бизнесе: https://yandex.ru/sprav/chain/<chain_id>/
chain_id: null
# rubric-id сети: хотя бы одна рубрика каждого филиала должна совпадать.
chain_rubric_ids: []
branches_dir: data/branches
baseline: data/baseline/yandex-export.xml
output: feed/feed.xml
"""


class WorkspaceError(Exception):
    pass


def dump_branch(branch: dict) -> str:
    return yaml.safe_dump(branch, allow_unicode=True, sort_keys=False, width=120)


def stringify(value):
    """YAML may parse ids and numbers as int/float; the feed is text."""
    if isinstance(value, dict):
        return {str(k): stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [stringify(v) for v in value]
    if isinstance(value, bool):
        return "1" if value else "0"
    return value if value is None else str(value)


@dataclass
class BuildResult:
    output: str
    branches: int
    updated: list[str]
    issues: list[Issue]


class Workspace:
    def __init__(self, root: str | Path | None = None):
        root = root or os.environ.get("YBIZ_WORKSPACE") or Path.cwd()
        self.root = Path(root).expanduser().resolve()
        cfg_path = self.root / "ybiz.yaml"
        self.cfg = dict(DEFAULTS)
        if cfg_path.exists():
            self.cfg |= yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    def path(self, key: str) -> Path:
        return self.root / self.cfg[key]

    @property
    def chain_rubrics(self) -> set[str]:
        return {str(r) for r in self.cfg["chain_rubric_ids"]}

    # --- branches -------------------------------------------------------

    def branch_file(self, company_id: str) -> Path:
        if "/" in company_id or company_id.startswith("."):
            raise WorkspaceError(f"bad company-id {company_id!r}")
        return self.path("branches_dir") / f"{company_id}.yaml"

    def load_branches(self) -> list[dict]:
        branches = []
        for f in sorted(self.path("branches_dir").glob("*.yaml")):
            b = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if str(b.get("company-id")) != f.stem:
                raise WorkspaceError(f"{f.name}: company-id {b.get('company-id')!r} does not match the file name")
            branches.append(stringify(b))
        return branches

    def get_branch(self, company_id: str) -> dict:
        f = self.branch_file(company_id)
        if not f.exists():
            raise WorkspaceError(f"no branch {company_id!r}")
        return stringify(yaml.safe_load(f.read_text(encoding="utf-8")))

    def save_branch(self, branch: dict) -> list[Issue]:
        """Write one branch after checking it; refuses to save a branch with errors."""
        branch = stringify({k: v for k, v in branch.items() if k != "actualization-date"})
        if unknown := set(branch) - set(ORDER):
            raise WorkspaceError(f"unknown keys {sorted(unknown)}; allowed: {list(ORDER)}")
        issues = validate_xsd(build_feed([branch])) + check_feed([branch], self.chain_rubrics)
        if any(i.level == "error" for i in issues):
            raise WorkspaceError("branch not saved:\n" + "\n".join(map(str, issues)))
        f = self.branch_file(str(branch.get("company-id")))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(dump_branch(branch), encoding="utf-8")
        return issues

    # --- pipeline -------------------------------------------------------

    def init(self) -> None:
        cfg = self.root / "ybiz.yaml"
        if not cfg.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            cfg.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        self.path("branches_dir").mkdir(parents=True, exist_ok=True)

    def import_export(self, xml_path: str | Path, force: bool = False) -> int:
        branches = parse_feed(Path(xml_path))
        out_dir = self.path("branches_dir")
        out_dir.mkdir(parents=True, exist_ok=True)
        existing = list(out_dir.glob("*.yaml"))
        if existing and not force:
            raise WorkspaceError(f"{out_dir} already has {len(existing)} branches; use force to overwrite them")
        for f in existing:
            f.unlink()
        for b in branches:
            b.pop("actualization-date", None)  # recomputed by build
            self.branch_file(str(b["company-id"])).write_text(dump_branch(b), encoding="utf-8")
        baseline = self.path("baseline")
        baseline.parent.mkdir(parents=True, exist_ok=True)
        if Path(xml_path).resolve() != baseline.resolve():
            shutil.copyfile(xml_path, baseline)
        return len(branches)

    def validate(self) -> list[Issue]:
        branches = self.load_branches()
        return validate_xsd(build_feed(branches)) + check_feed(branches, self.chain_rubrics)

    def baseline(self) -> list[dict] | None:
        path = self.path("baseline")
        return parse_feed(path) if path.exists() else None

    def diff(self) -> dict:
        base = self.baseline()
        if base is None:
            raise WorkspaceError(f"no baseline at {self.cfg['baseline']}; import a fresh Yandex export first")
        return asdict(diff_feeds(base, self.load_branches()))

    def build(self, allow_close: list[str] | None = None, no_baseline: bool = False, today: dt.date | None = None) -> BuildResult:
        branches = self.load_branches()
        if not branches:
            raise WorkspaceError("no branches: an empty feed would close the whole chain")

        base = self.baseline()
        if base is None and not no_baseline:
            raise WorkspaceError(f"no baseline at {self.cfg['baseline']}; import a Yandex export or pass no_baseline")
        if base is not None:
            removed = set(diff_feeds(base, branches).removed)
            if blocked := sorted(removed - set(allow_close or [])):
                raise WorkspaceError(
                    "these branches are in the Yandex baseline but not in the workspace, the feed would CLOSE them: "
                    + ", ".join(blocked)
                    + ". Restore them or confirm with allow_close."
                )

        output = self.path("output")
        previous = {str(b["company-id"]): b for b in parse_feed(output)} if output.exists() else {}
        stamp = (today or dt.date.today()).strftime("%d.%m.%Y")
        updated = []
        for b in branches:
            prev = previous.get(str(b["company-id"]))
            if prev is not None and comparable(prev) == comparable(b) and prev.get("actualization-date"):
                b["actualization-date"] = prev["actualization-date"]
            else:
                b["actualization-date"] = stamp
                updated.append(str(b["company-id"]))

        xml = build_feed(branches)
        issues = validate_xsd(xml) + check_feed(branches, self.chain_rubrics)
        if any(i.level == "error" for i in issues):
            raise WorkspaceError("feed not written:\n" + "\n".join(map(str, issues)))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(xml)
        return BuildResult(str(output), len(branches), updated, issues)
