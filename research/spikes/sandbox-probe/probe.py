"""Read-only sandbox availability probe; does not start daemons or execute code."""

import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def inspect_engine(name):
    path = shutil.which(name)
    result = {"installed": path is not None, "server_reachable": False}
    if not path:
        result["status"] = "not_installed"
        return result
    try:
        command = [path, "version", "--format", "json"]
        process = subprocess.run(command, capture_output=True, text=True, timeout=15)
        result["exit_code"] = process.returncode
        data = json.loads(process.stdout) if process.stdout.strip() else {}
        client = data.get("Client") or data.get("client") or {}
        server = data.get("Server") or data.get("server") or {}
        result["client_version"] = client.get("Version") or client.get("version")
        result["server_version"] = server.get("Version") or server.get("version")
        result["server_reachable"] = process.returncode == 0 and bool(server)
        result["status"] = "reachable" if result["server_reachable"] else "unavailable"
        # Raw output may contain usernames, sockets, or remote endpoint details.
        # Preserve the outcome without publishing those machine-specific values.
        result["output_handling"] = "Only version, exit code, and reachability retained."
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
    except (ValueError, OSError):
        result["status"] = "probe_error"
    return result


def main():
    engines = {name: inspect_engine(name) for name in ("docker", "podman")}
    tools = {
        name: {"installed": shutil.which(name) is not None, "runtime_verified": False}
        for name in ("bwrap", "sandbox-exec", "limactl", "colima", "qemu-system-aarch64")
    }
    result = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "probe_kind": "read_only_availability",
        "engines": engines,
        "other_tools": tools,
        "isolation_tests": {
            name: {"status": "not_run", "reason": "No sandbox policy or execution backend was started by this probe."}
            for name in ("filesystem_escape", "network_denial", "resource_limit", "cancel_cleanup", "revoked_permission_replay")
        },
        "limitations": [
            "An installed executable does not prove an available or correctly configured sandbox.",
            "No installation, image pull, daemon start, cloud upload, or generated-code execution.",
            "No security or cross-platform compatibility claim is established by this probe.",
        ],
    }
    output = Path(__file__).with_name("results.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
