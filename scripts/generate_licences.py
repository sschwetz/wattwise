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
import sys
import tempfile
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
LICENCE_PREFIXES = ("license", "licence", "copying", "notice")
EXACT_NAMES = ("license", "licence")
TEXT_SEPARATOR = "\n\n----------\n\n"

Lookup = Callable[[str, str], tuple[str, str | None]]
Opener = Callable[..., Any]


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    ecosystem: str
    license: str
    text: str | None


def licence_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Licence-like files directly in a directory, exact LICENSE/LICENCE names first."""
    matches = [p for p in directory.iterdir()
               if p.is_file() and p.name.lower().startswith(LICENCE_PREFIXES)]
    return sorted(matches, key=lambda p: (p.name.lower() not in EXACT_NAMES, p.name))


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def find_licence_text(package_dir: pathlib.Path) -> str | None:
    """Return the text of the preferred licence file in an installed npm package."""
    files = licence_files(package_dir)
    return read_text(files[0]) if files else None


def dist_info_text(dist_info: pathlib.Path) -> str | None:
    """Join every licence file in a dist-info dir, in sorted filename order."""
    files = licence_files(dist_info)
    licences = dist_info / "licenses"
    if licences.is_dir():
        files += [p for p in licences.rglob("*") if p.is_file()]
    if not files:
        return None
    ordered = sorted(files, key=lambda p: str(p.relative_to(dist_info)))
    return TEXT_SEPARATOR.join(read_text(p).strip() for p in ordered)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_info_texts(target: pathlib.Path) -> dict[tuple[str, str], str]:
    """Map (normalised name, version) to licence text for each dist-info in target."""
    found: dict[tuple[str, str], str] = {}
    for info in target.glob("*.dist-info"):
        name, _, version = info.name[: -len(".dist-info")].partition("-")
        text = dist_info_text(info)
        if text:
            found[(normalise(name), version)] = text
    return found


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


def install_service(service_dir: pathlib.Path, target: pathlib.Path, export_text: str) -> None:
    """Install the locked non-dev set of a service (its uv export text) into target without dependencies."""
    requirements = target.parent / f"{service_dir.name}-requirements.txt"
    requirements.write_text(export_text)
    cmd = ["uv", "pip", "install", "--quiet", "--no-deps", "--target", str(target),
           "-r", str(requirements)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as err:
        raise InstallError(f"installing {service_dir.name} failed: {err.stderr.strip()}") from err


class InstallError(RuntimeError):
    """Raised when a service's locked packages cannot be installed."""


def python_packages(
    service_dir: pathlib.Path, lookup: Lookup,
    installer: Callable[[pathlib.Path, pathlib.Path, str], None] = install_service,
) -> list[Package]:
    export_text = uv_export(service_dir)
    pairs = parse_export(export_text)
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "site"
        installer(service_dir, target, export_text)
        texts = dist_info_texts(target)
        return [Package(n, v, "python", lookup(n, v)[0], texts.get((normalise(n), v)))
                for n, v in pairs]


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
        try:
            packages += python_packages(ROOT / service, lookup)
        except (InstallError, subprocess.CalledProcessError) as err:
            sys.exit(f"error: {service}: {err}")
    document = build_document(packages)
    OUTPUT.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    entries = document["packages"]
    unknown = sum(1 for p in entries if p["license"] == "unknown")
    for eco in ("npm", "python"):
        print(f"{eco}: {sum(1 for p in entries if p['ecosystem'] == eco)} packages")
    print(f"unknown licences: {unknown}; distinct texts: {len(document['texts'])}")


if __name__ == "__main__":
    main()
