from __future__ import annotations

from pathlib import Path
import argparse
import html
import json
import re
import zipfile

from checker import Document, ReviewRunner, extract_visible_text


MEMO_PATTERN = re.compile(
    r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>'
)


def memo_records(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        section = "".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if re.fullmatch(r"Contents/section\d+\.xml", name)
        )
    records = []
    for match in MEMO_PATTERN.finditer(section):
        memo = extract_visible_text(match.group()).strip()
        end = section.find("<hp:ctrl><hp:fieldEnd", match.end())
        anchor_xml = section[match.end() : end if end >= 0 else match.end()]
        anchor = extract_visible_text(anchor_xml).strip()
        if any(keyword in memo for keyword in ("성취기준", "평가시기", "월별 계획")):
            records.append({"memo": memo, "anchor": anchor})
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    args = parser.parse_args()

    runner = ReviewRunner()
    output = []
    for path in sorted(args.submission_root.rglob("*.hwpx")):
        if path.parent.name != "1차 검토본":
            continue
        if any("백업" in part for part in path.parts):
            continue
        records = memo_records(path)
        if not records:
            continue
        document = Document.from_path(path)
        fresh = runner.review_file(path)
        output.append(
            {
                "file": str(path),
                "grade": document.grade,
                "subject": document.subject,
                "memos": records,
                "fresh_findings": [
                    {
                        "topic": finding.topic,
                        "anchor": finding.anchor_text,
                        "memo": finding.memo_text,
                    }
                    for finding in fresh
                    if any(keyword in f"{finding.topic}\n{finding.memo_text}" for keyword in ("성취기준", "평가시기", "월별 계획"))
                ],
            }
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
