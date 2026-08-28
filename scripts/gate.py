from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], repo_root: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "0"
    source_path = str(repo_root / "src")
    environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(command, cwd=repo_root, env=environment, text=True, capture_output=True, check=False)
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    result = {"command": command, "exitCode": completed.returncode}
    if completed.returncode != 0:
        print(json.dumps({"gate": "failed", "result": result}, sort_keys=True))
        raise SystemExit(completed.returncode)
    return result


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    results: list[dict[str, object]] = []
    uv = shutil.which("uv")
    if uv is None:
        print(json.dumps({"gate": "failed", "reason": "uv is not available"}, sort_keys=True))
        return 2
    results.append(run([uv, "lock", "--check"], repo_root))
    results.append(run([sys.executable, "-m", "marketplace_contracts.cli", "verify"], repo_root))
    results.append(run([sys.executable, "-m", "pytest", "-q"], repo_root))
    with tempfile.TemporaryDirectory(prefix="172x-mkdocs-") as temporary:
        results.append(
            run(
                [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", temporary],
                repo_root,
            )
        )
        results.append(
            run(
                [sys.executable, "-m", "marketplace_contracts.cli", "site", temporary],
                repo_root,
            )
        )
    print(json.dumps({"gate": "passed", "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
