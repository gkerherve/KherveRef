"""Check a built KherveRef: the frozen app reports the expected version
(--version), runs its --smoke-test offscreen (window, PDF import,
BibTeX, Git, citation styles) and answers MCP over stdio.

    python packaging/smoke_test.py dist/KherveRef/KherveRef.exe --version 0.24.N
    python packaging/smoke_test.py dist/KherveRef.app/Contents/MacOS/KherveRef
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    expected = None
    if "--version" in argv:
        i = argv.index("--version")
        expected = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    exe = argv[0]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                       timeout=120, env=env)
    reported = r.stdout.strip().removeprefix("KherveRef ")
    print(f"version reported: {reported}")
    if r.returncode != 0 or not reported:
        print("FAILED: --version", r.stdout, r.stderr)
        return 1
    if expected and reported.split("+")[0] != expected:
        print(f"FAILED: expected version {expected}")
        return 1
    r = subprocess.run([exe, "--smoke-test"], capture_output=True, text=True,
                       timeout=300, env=env)
    print(r.stdout, r.stderr, sep="\n")
    if r.returncode != 0 or "SMOKE OK" not in r.stdout:
        print("FAILED: --smoke-test")
        return 1
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = subprocess.run([exe, "--mcp-server"], input="".join(
        json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
        timeout=120, env=env)
    replies = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    if len(replies) != 2 or not replies[1]["result"]["tools"]:
        print("FAILED: --mcp-server", r.stdout, r.stderr)
        return 1
    print(f"MCP OK ({len(replies[1]['result']['tools'])} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
