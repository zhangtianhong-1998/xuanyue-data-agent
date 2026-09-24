"""Verify pinned upstream checkouts; --fetch only creates missing directories."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], text=True, capture_output=True, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Download absent repositories at pinned commits")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    for manifest in sorted((ROOT / "research/upstreams").glob("*-sources.json")):
        for source in json.loads(manifest.read_text())["sources"]:
            row = {"id": source.get("name", source.get("id")), "manifest": str(manifest.relative_to(ROOT))}
            try:
                url = source.get("url", source.get("repository"))
                commit = source["commit"]
                path = (ROOT / source["path"]).resolve()
                if path.parent != ROOT / "research/upstreams" or not re.fullmatch(r"[0-9a-f]{40}", commit):
                    raise ValueError("Unsafe path or invalid commit")
                if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", url):
                    raise ValueError("Unexpected source URL")
                if not path.exists() and args.fetch:
                    path.mkdir()
                    for command in [("init",), ("remote", "add", "origin", url),
                                    ("fetch", "--depth", "1", "origin", commit),
                                    ("checkout", "--detach", commit)]:
                        result = git(path, *command)
                        if result.returncode:
                            raise RuntimeError(result.stderr.strip())
                if not (path / ".git").exists():
                    raise ValueError("Checkout absent; run with --fetch to obtain it")
                head = git(path, "rev-parse", "HEAD")
                origin = git(path, "remote", "get-url", "origin")
                status = git(path, "status", "--porcelain")
                fsck = git(path, "fsck", "--connectivity-only")
                submodules = git(path, "submodule", "status")
                checks = {"commit_matches": head.returncode == 0 and head.stdout.strip() == commit,
                          "origin_matches": origin.returncode == 0 and origin.stdout.strip() == url,
                          "working_tree_clean": status.returncode == 0 and not status.stdout.strip(),
                          "connectivity_ok": fsck.returncode == 0,
                          "no_declared_submodules": not (path / ".gitmodules").exists() and
                              submodules.returncode == 0 and not submodules.stdout.strip()}
                license_info = source.get("license")
                license_path = source.get("license_file") if isinstance(license_info, str) else license_info["root_file"]
                license_hash = hashlib.sha256((path / license_path).read_bytes()).hexdigest()
                expected = source.get("license_sha256")
                if expected:
                    checks["license_hash_matches"] = license_hash == expected
                row.update({"commit": head.stdout.strip(), "checks": checks,
                            "license_file": license_path, "license_sha256": license_hash,
                            "passed": all(checks.values())})
            except Exception as exc:
                row.update({"passed": False, "error": str(exc)})
            rows.append(row)
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Git identity/connectivity and root license hashes only; not runtime or full dependency audit",
              "sources": rows, "passed": bool(rows) and all(row["passed"] for row in rows)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"passed": report["passed"], "sources": [{"id": r["id"], "passed": r["passed"]}
                                                             for r in rows]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
