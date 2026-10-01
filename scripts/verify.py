"""Repeatable local model/HTTP/profile checks; never loads a personal profile."""

import ast
import json
import platform
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for directory in [ROOT / "applybot", ROOT / "tests", ROOT / "scripts"]:
    for source in directory.rglob("*.py"):
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.discover(str(ROOT / "tests"))
)
output = ROOT / ".verification" / "model.json"
output.parent.mkdir(exist_ok=True)
output.write_text(
    json.dumps(
        {
            "passed": result.wasSuccessful(),
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "python": platform.python_version(),
            "syntax": "all project Python modules parsed",
        },
        indent=2,
    ),
    encoding="utf-8",
)
raise SystemExit(0 if result.wasSuccessful() else 1)
