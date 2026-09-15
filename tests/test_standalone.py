"""Guardrail 1 - Mission Machine stands alone.

The independence claim in ``docs/reuse-assessment.md`` is only worth making if
it is checked. These tests boot the demonstrator in a fresh, isolated
interpreter and scan the source for anything that would mean it had taken a
dependency - on a third-party package, or on RODOT or capacity-machine.

Ported from capacity-machine's ``tests/test_standalone_boot.py``; see
``docs/reuse-assessment.md``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "mission_machine"

#: Names that would mean we had imported a neighbouring codebase rather than
#: adopting a design from it.
FORBIDDEN_IMPORT_PATTERNS = (
    r"^\s*(?:from|import)\s+[\w.]*\brodot\b",
    r"^\s*(?:from|import)\s+[\w.]*capacity_machine",
    r"^\s*(?:from|import)\s+[\w.]*solid_?soup",
    r"^\s*(?:from|import)\s+[\w.]*\bmica\b",
    r"^\s*(?:from|import)\s+[\w.]*interop_capacity",
)

#: Modules that reach the network. The domain must not touch them at all; only
#: the UI server may, and only to listen locally.
NETWORK_MODULES = {
    "http", "socket", "socketserver", "ssl", "urllib", "ftplib", "smtplib",
    "telnetlib", "xmlrpc", "asyncio", "requests", "httpx", "aiohttp",
}

UI_MODULES = {"mission_machine.ui.server"}


def python_sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            names.update(alias.name.split(".")[0] for alias in child.names)
        elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
            names.add(child.module.split(".")[0])
    return names


def module_level_imports(path: Path) -> set[str]:
    """Imports that run when the module is loaded.

    Imports inside a function body are deliberately excluded: that is how an
    optional backend is reached, and the point of the guarantee is that nothing
    third-party is needed to *import and run* the demonstrator.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names |= _imported_names(item)
            continue
        names |= _imported_names(node)
    return names


def deferred_imports(path: Path) -> set[str]:
    """Imports that only run when a particular function is called."""

    return _imported_names(ast.parse(path.read_text(encoding="utf-8"))) - module_level_imports(path)


def optional_extra_packages() -> set[str]:
    """Package names declared in ``[project.optional-dependencies]``."""

    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[project.optional-dependencies]", 1)[1].split("[build-system]", 1)[0]
    return {
        re.split(r"[<>=!~\[]", entry)[0].strip().replace("-", "_")
        for entry in re.findall(r'"([^"]+)"', block)
    }


class IsolationTests(unittest.TestCase):
    def test_no_neighbouring_codebase_is_imported(self) -> None:
        offenders: list[str] = []
        for path in python_sources():
            text = path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_IMPORT_PATTERNS:
                for match in re.finditer(pattern, text, flags=re.MULTILINE | re.IGNORECASE):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0).strip()}")
        self.assertEqual(offenders, [], f"Mission Machine must not import neighbouring code: {offenders}")

    def test_declared_runtime_dependencies_are_empty(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        block = pyproject.split("dependencies = ", 1)[1].split("\n", 1)[0].strip()
        self.assertEqual(block, "[]", "the demonstrator must declare no runtime dependencies")
        for marker in ("rodot", "capacity-machine", "capacity_machine", "solid-soup", "mica"):
            self.assertNotIn(marker, pyproject.lower())

    def test_importing_the_package_needs_only_the_standard_library(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"mission_machine", "__future__"}
        offenders: list[str] = []
        for path in python_sources():
            for name in module_level_imports(path):
                if name not in allowed:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
        self.assertEqual(offenders, [], f"third-party imports at module level: {offenders}")

    def test_every_deferred_third_party_import_is_a_declared_optional_extra(self) -> None:
        """An optional backend may be imported inside a function - and only if it is declared."""

        allowed = set(sys.stdlib_module_names) | {"mission_machine", "__future__"}
        extras = optional_extra_packages()
        offenders: list[str] = []
        for path in python_sources():
            for name in deferred_imports(path):
                if name in allowed:
                    continue
                if name not in extras:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
        self.assertEqual(
            offenders,
            [],
            f"undeclared third-party imports: {offenders}. Declare them under "
            "[project.optional-dependencies] or remove them.",
        )

    def test_the_domain_never_reaches_the_network(self) -> None:
        offenders: list[str] = []
        for path in python_sources():
            name = module_name(path)
            if name in UI_MODULES:
                continue
            for imported in (module_level_imports(path) | deferred_imports(path)) & NETWORK_MODULES:
                offenders.append(f"{name}: {imported}")
        self.assertEqual(
            offenders,
            [],
            f"only the UI server may import networking; found {offenders}",
        )


class CleanInterpreterBootTests(unittest.TestCase):
    """The demonstrator must run with nothing on the path but this repository."""

    def _run(self, code: str) -> subprocess.CompletedProcess:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "HOME"}
        }
        # -E ignores PYTHONPATH, -s ignores the user site directory. The
        # repository itself stays importable from the working directory, which
        # is the whole point: nothing else is needed.
        return subprocess.run(
            [sys.executable, "-E", "-s", "-c", code],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_planning_runs_in_an_isolated_interpreter(self) -> None:
        result = self._run(
            "import json, sys;"
            "from mission_machine.cli import main;"
            "sys.exit(main(['--json', 'configure', '--fast']))"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["plan"]["options"]), 3)

    def test_the_milp_check_runs_in_an_isolated_interpreter(self) -> None:
        result = self._run(
            "import sys; from mission_machine.cli import main; sys.exit(main(['verify']))"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("physics PASS", result.stdout)

    def test_no_optional_solver_backend_is_required(self) -> None:
        result = self._run(
            "from mission_machine.planning import milp;"
            "print(milp.available_backends())"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
