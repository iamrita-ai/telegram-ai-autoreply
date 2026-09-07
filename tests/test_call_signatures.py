"""Static check that every cross-module call matches its function's signature.

This exists because of a real outage. The multi-user refactor changed
``_launch(client)`` to ``_launch(client, owner)`` and one of the two call
sites was missed. Every unit test passed - because they patched ``_launch``
- and the service crash-looped on Render with::

    TypeError: _launch() missing 1 required positional argument: 'owner'

Mocks cannot catch that: a mock accepts any arguments. So instead of trusting
tests to exercise every call site, this walks the AST of every module and
binds each call it can resolve against the real signature.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

PROJECT_MODULES = [
    "main",
    "config",
    "core.humanize",
    "core.personas",
    "core.rich",
    "core.safety",
    "core.voice",
    "database.mongo",
    "handlers.ai",
    "handlers.control",
    "handlers.scheduler",
    "handlers.userbot",
]

SOURCE_FILES = sorted(
    path
    for path in Path().rglob("*.py")
    if not any(part in {"tests", ".venv", "scripts"} for part in path.parts)
)


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local name -> module path, for imports we care about."""
    known = set(PROJECT_MODULES)
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    mapping[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    mapping[alias.asname or alias.name] = candidate
    return mapping


def _local_functions(tree: ast.Module) -> set[str]:
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _describe(call: ast.Call) -> tuple[int, list[str], bool]:
    """(positional count, keyword names, uses unpacking)."""
    starred = any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    )
    positional = sum(1 for a in call.args if not isinstance(a, ast.Starred))
    keywords = [k.arg for k in call.keywords if k.arg]
    return positional, keywords, starred


def _check(function, call: ast.Call, label: str, skip_self: bool = False) -> str | None:
    positional, keywords, starred = _describe(call)
    if starred:
        return None  # cannot reason about *args / **kwargs statically
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return None
    args = [inspect.Parameter.empty] * positional
    kwargs = dict.fromkeys(keywords, inspect.Parameter.empty)
    try:
        signature.bind(*args, **kwargs)
    except TypeError as exc:
        return f"{label}: {exc}"
    return None


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p))
def test_calls_match_signatures(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules = _imports(tree)
    local = _local_functions(tree)
    module_self = importlib.import_module(
        str(path.with_suffix("")).replace("/", ".").removesuffix(".__init__")
    )

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func

        # module.function(...)
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            module_name = modules.get(target.value.id)
            if not module_name:
                continue
            module = importlib.import_module(module_name)
            function = getattr(module, target.attr, None)
            if function is None or not callable(function):
                continue
            if inspect.isclass(function):
                continue
            label = f"{path}:{node.lineno} {target.value.id}.{target.attr}()"
            problem = _check(function, node, label)
            if problem:
                problems.append(problem)

        # function(...) defined in this same module
        elif isinstance(target, ast.Name) and target.id in local:
            function = getattr(module_self, target.id, None)
            if function is None or not callable(function):
                continue
            label = f"{path}:{node.lineno} {target.id}()"
            problem = _check(function, node, label)
            if problem:
                problems.append(problem)

    assert not problems, "call does not match its signature:\n  " + "\n  ".join(problems)
