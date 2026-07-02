from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
LIVE_DATA_SRC = SRC / "pumpagent" / "live_data"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


FORBIDDEN_EXACT_IMPORTS = {
    "pumpagent.runtime.orchestrator",
    "pumpagent.runtime.modules",
    "requests",
    "urllib",
    "urllib.request",
    "httpx",
    "aiohttp",
    "websocket",
    "websockets",
    "sqlite3",
    "sqlalchemy",
    "psycopg2",
    "pymongo",
}

FORBIDDEN_PREFIXES = (
    "pumpagent.runtime.orchestrator.",
    "pumpagent.runtime.modules.",
    "requests.",
    "urllib.",
    "httpx.",
    "aiohttp.",
    "websocket.",
    "websockets.",
    "sqlite3.",
    "sqlalchemy.",
    "psycopg2.",
    "pymongo.",
)


class LiveDataDependencyBoundaryTests(unittest.TestCase):
    def test_production_live_data_avoids_forbidden_dependencies(self) -> None:
        violations: list[str] = []

        for path in LIVE_DATA_SRC.rglob("*.py"):
            if "adapters" in path.relative_to(LIVE_DATA_SRC).parts:
                continue

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in _imports_from(tree):
                if _is_forbidden(imported_module):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported_module}")

        self.assertEqual(violations, [])


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


def _is_forbidden(module_name: str) -> bool:
    if module_name in FORBIDDEN_EXACT_IMPORTS:
        return True
    return module_name.startswith(FORBIDDEN_PREFIXES)


if __name__ == "__main__":
    unittest.main()
