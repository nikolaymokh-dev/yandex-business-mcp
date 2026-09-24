"""MCP server: manage a Yandex Business chain feed from an agent.

The workspace (private client data) is chosen by the YBIZ_WORKSPACE env var or the
`workspace` argument of each tool. Write tools never publish anything to Yandex:
they only change files in the workspace; publishing the feed URL is a manual step.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from yandex_business_mcp.workspace import Workspace, WorkspaceError

mcp = FastMCP("Yandex Business")


def _ws(workspace: str | None) -> Workspace:
    return Workspace(workspace)


def _issues(issues) -> list[str]:
    return [str(i) for i in issues]


def _guard(fn):
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (WorkspaceError, ValueError, OSError) as e:
            return {"error": str(e)}
    return run


@mcp.tool()
def ybiz_status(workspace: str | None = None) -> dict[str, Any]:
    """Сводка по workspace: конфиг сети, число филиалов, есть ли baseline и собранный фид."""
    def run():
        ws = _ws(workspace)
        return {
            "root": str(ws.root),
            "config": ws.cfg,
            "branches": len(ws.load_branches()),
            "baseline": ws.path("baseline").exists(),
            "feed": ws.path("output").exists(),
        }
    return _guard(run)()


@mcp.tool()
def ybiz_list_branches(workspace: str | None = None) -> Any:
    """Список филиалов: company-id, название, адрес, рубрики."""
    def run():
        return [
            {
                "company-id": b["company-id"],
                "name": b.get("name"),
                "address": b.get("address"),
                "rubric-id": b.get("rubric-id"),
            }
            for b in _ws(workspace).load_branches()
        ]
    return _guard(run)()


@mcp.tool()
def ybiz_get_branch(company_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Полные данные одного филиала (как в data/branches/<company-id>.yaml)."""
    return _guard(lambda: _ws(workspace).get_branch(company_id))()


@mcp.tool()
def ybiz_update_branch(company_id: str, changes: dict[str, Any], workspace: str | None = None) -> dict[str, Any]:
    """Изменить поля филиала. `changes` заменяет ключи верхнего уровня целиком
    (например {"working-time": {"ru": "ежедн. 10:00-22:00"}}); значение null удаляет ключ.
    Филиал с ошибками валидации не сохраняется. Фид не пересобирается — вызови ybiz_build."""
    def run():
        ws = _ws(workspace)
        branch = ws.get_branch(company_id)
        if "company-id" in changes and str(changes["company-id"]) != company_id:
            raise WorkspaceError("company-id is immutable; a moved branch needs a new branch with a new id")
        for key, value in changes.items():
            if value is None:
                branch.pop(key, None)
            else:
                branch[key] = value
        return {"saved": company_id, "issues": _issues(ws.save_branch(branch))}
    return _guard(run)()


@mcp.tool()
def ybiz_add_branch(branch: dict[str, Any], workspace: str | None = None) -> dict[str, Any]:
    """Добавить новый филиал. Обязательные поля: company-id, name, address, phone, url,
    working-time, rubric-id. Формат как у ybiz_get_branch; допустимые ключи — см. README."""
    def run():
        ws = _ws(workspace)
        cid = str(branch.get("company-id", ""))
        if ws.branch_file(cid).exists():
            raise WorkspaceError(f"branch {cid!r} already exists; use ybiz_update_branch")
        return {"saved": cid, "issues": _issues(ws.save_branch(branch))}
    return _guard(run)()


@mcp.tool()
def ybiz_import_export(xml_path: str, force: bool = False, workspace: str | None = None) -> dict[str, Any]:
    """Импорт XML-выгрузки из кабинета Яндекс Бизнеса в YAML-филиалы; сохраняет её как baseline.
    force=True перезаписывает существующие YAML (локальные несохранённые правки пропадут)."""
    return _guard(lambda: {"imported": _ws(workspace).import_export(xml_path, force)})()


@mcp.tool()
def ybiz_validate(workspace: str | None = None) -> dict[str, Any]:
    """Проверка всех филиалов: XSD Яндекса + правила из документации."""
    def run():
        issues = _ws(workspace).validate()
        return {"errors": sum(i.level == "error" for i in issues), "issues": _issues(issues)}
    return _guard(run)()


@mcp.tool()
def ybiz_diff(workspace: str | None = None) -> dict[str, Any]:
    """Что фид изменит относительно baseline: added, removed (будут ЗАКРЫТЫ в Яндексе), changed."""
    return _guard(lambda: _ws(workspace).diff())()


@mcp.tool()
def ybiz_build(allow_close: list[str] | None = None, no_baseline: bool = False, workspace: str | None = None) -> dict[str, Any]:
    """Собрать feed/feed.xml. Отказывается, если фид закроет филиалы из baseline, пока их
    company-id не переданы в allow_close — передавай только по явному подтверждению пользователя."""
    def run():
        r = _ws(workspace).build(allow_close, no_baseline)
        return {"output": r.output, "branches": r.branches, "updated": r.updated, "issues": _issues(r.issues)}
    return _guard(run)()


def main() -> None:
    mcp.run()
