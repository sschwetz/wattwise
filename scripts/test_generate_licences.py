"""Tests for generate_licences."""
import json
import pathlib
import urllib.error
from typing import Any

import generate_licences as gl


def write_npm_fixture(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    lock = root / "package-lock.json"
    lock.write_text(json.dumps({"packages": {
        "": {"name": "app"},
        "node_modules/react": {"version": "19.0.0", "license": "MIT"},
        "node_modules/vitest": {"version": "1.0.0", "license": "MIT", "dev": True},
        "node_modules/@scope/pkg": {"version": "2.0.0", "license": "ISC"},
    }}))
    modules = root / "node_modules"
    (modules / "react").mkdir(parents=True)
    (modules / "react" / "LICENSE").write_text("MIT text")
    return lock, modules


def test_npm_skips_dev_and_reads_text(tmp_path: pathlib.Path) -> None:
    lock, modules = write_npm_fixture(tmp_path)
    pkgs = gl.npm_packages(lock, modules)
    assert sorted(p.name for p in pkgs) == ["@scope/pkg", "react"]
    react = next(p for p in pkgs if p.name == "react")
    assert (react.license, react.text, react.ecosystem) == ("MIT", "MIT text", "npm")
    assert next(p for p in pkgs if p.name == "@scope/pkg").text is None


def fake_urlopen(payloads: dict[str, Any]) -> Any:
    def opener(url: str, timeout: int) -> Any:
        for key, payload in payloads.items():
            if key in url:
                if payload is None:
                    raise urllib.error.HTTPError(url, 404, "nf", None, None)  # type: ignore[arg-type]
                body = json.dumps(payload).encode()
                return type("R", (), {
                    "read": lambda self: body,
                    "__enter__": lambda self: self,
                    "__exit__": lambda self, *a: None,
                })()
        raise AssertionError(url)
    return opener


def test_pypi_lookup_404_is_unknown() -> None:
    lookup = gl.make_lookup(fake_urlopen({"/gone/": None}))
    assert lookup("gone", "1.0") == ("unknown", None)


def test_pypi_lookup_precedence() -> None:
    long_text = "L" * 150
    lookup = gl.make_lookup(fake_urlopen({
        "/a/": {"info": {"license_expression": "MIT", "license": long_text}},
        "/b/": {"info": {"license": "BSD", "license_expression": None}},
        "/c/": {"info": {"license": long_text, "classifiers": [
            "Programming Language :: Python", "License :: OSI Approved :: Apache Software License"]}},
    }))
    assert lookup("a", "1") == ("MIT", long_text)
    assert lookup("b", "1") == ("BSD", None)
    assert lookup("c", "1") == ("Apache Software License", long_text)


def test_python_packages_parses_export(monkeypatch: Any, tmp_path: pathlib.Path) -> None:
    out = "# c\nfoo==1.0 ; python_version < '4'\n    # via x\n-e .\nbar==2.0\n"
    monkeypatch.setattr(gl, "uv_export", lambda d: out)
    pkgs = gl.python_packages(tmp_path, lambda n, v: ("MIT", None))
    assert [(p.name, p.version) for p in pkgs] == [("foo", "1.0"), ("bar", "2.0")]


def test_build_document_dedupes_and_sorts() -> None:
    P = gl.Package
    doc = gl.build_document([
        P("Zed", "1", "python", "MIT", "same"),
        P("alpha", "2", "npm", "MIT", "same"),
        P("alpha", "1", "npm", "MIT", "same"),
        P("alpha", "1", "npm", "MIT", "same"),
        P("beta", "1", "python", "unknown", None),
    ])
    assert [(p["name"], p["version"]) for p in doc["packages"]] == [
        ("alpha", "1"), ("alpha", "2"), ("beta", "1"), ("Zed", "1")]
    assert len(doc["texts"]) == 1
    ids = {p["text_id"] for p in doc["packages"]}
    assert ids == {next(iter(doc["texts"])), None}
    assert doc["packages"][2] == {"name": "beta", "version": "1", "ecosystem": "python",
                                  "license": "unknown", "text_id": None}
    assert doc["generated_at"].endswith("Z")
