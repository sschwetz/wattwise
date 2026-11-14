"""Check that relative links and anchors in the Markdown docs resolve, and that no table has a blank line inside it.

Run from the repository root: python3 scripts/check_docs.py
Exits 1 when it finds a problem.
"""

import glob
import os
import re
import sys

FILES = sorted(
    {"README.md", "CLAUDE.md"}
    | set(glob.glob("docs/*.md"))
    | set(glob.glob("contracts/**/*.md", recursive=True))
    | set(glob.glob("webapp/src/docs/*.md"))
)
FENCE = re.compile(r"```.*?```", re.S)


def slug(heading: str) -> str:
    heading = re.sub(r"[`*_]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def prose(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return FENCE.sub("", f.read())


def anchors_of(path: str) -> set[str]:
    return {slug(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.*)$", prose(path), re.M)}


def check_links(problems: list[str]) -> None:
    anchors = {f: anchors_of(f) for f in FILES}
    for f in FILES:
        for m in re.finditer(r"\]\(([^)\s]+)\)", prose(f)):
            link = m.group(1)
            if link.startswith(("http", "mailto")):
                continue
            path, _, anchor = link.partition("#")
            target = os.path.normpath(os.path.join(os.path.dirname(f), path)) if path else f
            if not os.path.exists(target):
                problems.append(f"{f}: missing file {link}")
            elif anchor and target in anchors and anchor not in anchors[target]:
                problems.append(f"{f}: missing anchor {link}")


def check_tables(problems: list[str]) -> None:
    for f in FILES:
        lines = prose(f).splitlines()
        for i in range(1, len(lines) - 1):
            starts_new_table = i + 2 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 2])
            if lines[i].strip() == "" and lines[i - 1].startswith("|") and lines[i + 1].startswith("|") and not starts_new_table:
                problems.append(f"{f}: blank line splits a table near '{lines[i + 1][:40]}'")


def main() -> int:
    problems: list[str] = []
    check_links(problems)
    check_tables(problems)
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
