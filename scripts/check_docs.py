"""Validate local links/anchors in the current public documentation."""
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md")]
DOCUMENTS += sorted((ROOT / "docs").glob("*.md"))


def without_code(text):
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.M | re.S)


def anchors(path):
    seen, result = {}, set()
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*$", without_code(path.read_text()), re.M):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        index = seen.get(slug, 0)
        seen[slug] = index + 1
        result.add(slug if not index else f"{slug}-{index}")
    return result


def main():
    failures = []
    for source in DOCUMENTS:
        for link in re.findall(r"\]\(([^)]+)\)", without_code(source.read_text())):
            if re.match(r"[a-z][a-z0-9+.-]*:", link):
                continue
            target, _, anchor = unquote(link).partition("#")
            path = (source.parent / target).resolve() if target else source
            if not path.exists():
                failures.append(f"{source.relative_to(ROOT)}: missing {link}")
            elif anchor and path.suffix == ".md" and anchor not in anchors(path):
                failures.append(f"{source.relative_to(ROOT)}: missing anchor {link}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"PASS: local documentation links and anchors in {len(DOCUMENTS)} current documents.")


if __name__ == "__main__":
    main()
