#!/usr/bin/env python3
"""Write webapp/public/licences.json, the package list shown in the About dialog.

Standard library only, so CI needs no install. Python packages come from
`uv export` in each service directory; npm packages from webapp/package-lock.json.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON_SERVICES = [
    "core-api",
    "envoy-collector",
    "weatherlink-local-collector",
    "amber-pricing-collector",
    "local-pricing-collector",
    "actron-neo-actor",
]
OUTPUT = ROOT / "webapp" / "public" / "licences.json"
PYPI_URL = "https://pypi.org/pypi/{name}/{version}/json"
LONG_TEXT = 100
LICENCE_FILE_GLOBS = ("LICENSE*", "LICENCE*", "COPYING*")

Lookup = Callable[[str, str], tuple[str, str | None]]
Opener = Callable[..., Any]


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    ecosystem: str
    license: str
    text: str | None


def find_licence_text(package_dir: pathlib.Path) -> str | None:
    """Return the text of the first licence-like file in an installed package."""
    for pattern in LICENCE_FILE_GLOBS:
        for path in sorted(package_dir.glob(pattern)):
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
    return None


def npm_packages(lockfile: pathlib.Path, node_modules: pathlib.Path) -> list[Package]:
    entries: dict[str, dict[str, Any]] = json.loads(lockfile.read_text())["packages"]
    found: list[Package] = []
    for key, entry in entries.items():
        if not key or entry.get("dev"):
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        installed = node_modules.parent / key
        text = find_licence_text(installed) if installed.is_dir() else None
        found.append(Package(name, entry["version"], "npm", str(entry.get("license") or "unknown"), text))
    return found


def uv_export(service_dir: pathlib.Path) -> str:
    cmd = ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project"]
    return subprocess.run(cmd, cwd=service_dir, check=True, capture_output=True, text=True).stdout


def parse_export(output: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in output.splitlines():
        line = line.split(";", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==(\S+)", line)
        if match and not line.startswith(("#", "-e")):
            pairs.append((match.group(1), match.group(2)))
    return pairs


def pick_licence(info: dict[str, Any]) -> tuple[str, str | None]:
    """Choose the licence name and, when info.license holds a full text, the text."""
    raw = (info.get("license") or "").strip()
    text = raw if len(raw) >= LONG_TEXT else None
    if info.get("license_expression"):
        return str(info["license_expression"]), text
    if raw and text is None:
        return raw, None
    for classifier in info.get("classifiers") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip(), text
    return "unknown", text


def fetch_json(opener: Opener, url: str) -> dict[str, Any] | None:
    """GET a URL with a 15 s timeout, retrying once on a transport error."""
    for attempt in range(2):
        try:
            with opener(url, timeout=15) as response:
                return json.loads(response.read())  # type: ignore[no-any-return]
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 1:
                return None
    return None


def make_lookup(opener: Opener = urllib.request.urlopen) -> Lookup:
    """Return a PyPI lookup that fetches each (name, version) once."""

    @lru_cache(maxsize=None)
    def lookup(name: str, version: str) -> tuple[str, str | None]:
        data = fetch_json(opener, PYPI_URL.format(name=name, version=version))
        if data is None:
            return "unknown", None
        return pick_licence(data.get("info") or {})

    return lookup


def python_packages(service_dir: pathlib.Path, lookup: Lookup) -> list[Package]:
    pairs = parse_export(uv_export(service_dir))
    return [Package(n, v, "python", *lookup(n, v)) for n, v in pairs]


def build_document(packages: list[Package]) -> dict[str, Any]:
    unique = {(p.ecosystem, p.name, p.version): p for p in packages}
    texts: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    for pkg in sorted(unique.values(), key=lambda p: (p.name.lower(), p.version)):
        text_id = None
        if pkg.text:
            text_id = hashlib.sha256(pkg.text.encode()).hexdigest()[:12]
            texts[text_id] = pkg.text
        entries.append({"name": pkg.name, "version": pkg.version, "ecosystem": pkg.ecosystem,
                        "license": pkg.license, "text_id": text_id})
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"generated_at": generated, "texts": texts, "packages": entries}


def main() -> None:
    lookup = make_lookup()
    packages = npm_packages(ROOT / "webapp" / "package-lock.json", ROOT / "webapp" / "node_modules")
    for service in PYTHON_SERVICES:
        packages += python_packages(ROOT / service, lookup)
    document = build_document(packages)
    OUTPUT.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    entries = document["packages"]
    unknown = sum(1 for p in entries if p["license"] == "unknown")
    for eco in ("npm", "python"):
        print(f"{eco}: {sum(1 for p in entries if p['ecosystem'] == eco)} packages")
    print(f"unknown licences: {unknown}; distinct texts: {len(document['texts'])}")


if __name__ == "__main__":
    main()
