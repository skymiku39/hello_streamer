"""New application buttons must inherit our style instead of toolkit defaults."""

import ast
from pathlib import Path


def test_buttons_use_the_shared_application_component():
    violations = []
    for path in (Path(__file__).resolve().parents[1] / "stream_monitor").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if name in {"CTkButton", "Button"}:
                violations.append(f"{path.name}:{node.lineno}")
    assert not violations, "Use AppButton for application buttons: " + ", ".join(violations)
