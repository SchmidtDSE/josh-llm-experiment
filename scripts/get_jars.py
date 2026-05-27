#!/usr/bin/env python3
"""Download or refresh the Josh fat jars for local use.

Host-side helper (run via `pixi run get-jars`). Unlike the josh-models
repo's get-jars — which goes through joshpy's `download_jars` — this repo
has no joshpy dependency and pulls jars the same way the image build does:
SchmidtDSE/josh publishes rolling per-branch fat jars at
`https://joshsim.org/dist/<branch>/joshsim-fat.jar` (the project has no
tagged releases, so sha256 is the reproducibility anchor). See
scripts/install_josh.sh, which bakes the `main` jar into the agent/scorer
images at build time.

Two builds are fetched:

  - dev  — the rolling `dev` branch build. As of 2026-05 this is the build
           that carries the `mcp` subcommand (SchmidtDSE/josh#440), so it's
           the one to smoke-test the Josh MCP server with (see MCP_UPDATE.md).
  - main — the rolling stable build install_josh.sh uses by default.

Each jar lands at jar/<branch>/joshsim-fat.jar (gitignored) with a matching
.sha256 sidecar in the exact format install_josh.sh records, so a hash
captured here can be copied straight into a pinned image build, e.g.:

    JOSH_JAR_URL=https://joshsim.org/dist/dev/joshsim-fat.jar \\
        docker build ... (install_josh.sh honors JOSH_JAR_URL)
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JAR_DIR = REPO_ROOT / "jar"
DIST_BASE = "https://joshsim.org/dist"
BRANCHES = ("dev", "main")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short(value: str | None) -> str:
    return f"{value[:12]}..." if value else "none"


def _refresh(branch: str) -> None:
    url = f"{DIST_BASE}/{branch}/joshsim-fat.jar"
    dest = JAR_DIR / branch / "joshsim-fat.jar"
    sha_path = dest.parent / (dest.name + ".sha256")
    old_hash = sha_path.read_text().split()[0] if sha_path.exists() else None

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".tmp")
    print(f"  {branch}: fetching {url}")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:  # noqa: S310 (trusted host)
        last_modified = resp.headers.get("Last-Modified", "unknown")
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)

    new_hash = _sha256(dest)
    sha_path.write_text(f"{new_hash}  joshsim-fat.jar\n")

    if old_hash == new_hash:
        print(f"  {branch}: up to date ({_short(new_hash)}, {last_modified})")
    else:
        print(f"  {branch}: updated {_short(old_hash)} -> {_short(new_hash)} ({last_modified})")
    print(f"           {dest.relative_to(REPO_ROOT)}")


def main() -> None:
    print("Refreshing Josh fat jars (rolling per-branch builds)...")
    for branch in BRANCHES:
        _refresh(branch)
    dev_jar = (JAR_DIR / "dev" / "joshsim-fat.jar").relative_to(REPO_ROOT)
    print()
    print("Smoke-test the MCP server locally with the dev jar:")
    print(f"  java -jar {dev_jar} mcp")


if __name__ == "__main__":
    main()
