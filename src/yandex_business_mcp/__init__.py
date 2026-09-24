"""Yandex Business MCP: manage a chain's branches via the official XML feed."""


def main() -> None:
    from yandex_business_mcp.server import main as run

    run()
