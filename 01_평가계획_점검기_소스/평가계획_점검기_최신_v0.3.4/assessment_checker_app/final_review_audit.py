from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import re
import zipfile

from checker import Document, RuleEngine, extract_visible_text


MEMO_PATTERN = re.compile(
    r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>'
)


def memo_texts(path: Path) -> list[str]:
    result: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"Contents/section\d+\.xml", name):
                continue
            section = archive.read(name).decode("utf-8", errors="ignore")
            result.extend(extract_visible_text(match.group()).strip() for match in MEMO_PATTERN.finditer(section))
    return result


def is_active_memo(text: str) -> bool:
    return "수정 완료" not in text and "확인 완료" not in text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    args = parser.parse_args()

    files = [
        path
        for path in args.submission_root.rglob("*.hwpx")
        if path.parent.name == "1차 검토본" and "백업" not in str(path)
    ]
    missing: list[tuple[str, str, str]] = []
    duplicate_memos: list[tuple[str, str, int]] = []
    suspicious: list[tuple[str, str]] = []
    total_memos = 0
    active_memos = 0

    for path in sorted(files):
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                suspicious.append((path.name, f"손상 항목: {bad}"))
        memos = memo_texts(path)
        active = [memo for memo in memos if is_active_memo(memo)]
        total_memos += len(memos)
        active_memos += len(active)

        counts = Counter(active)
        for memo, count in counts.items():
            if count > 1:
                duplicate_memos.append((path.name, memo.splitlines()[0], count))

        for memo in active:
            if "평가시기 표기 형식 확인" in memo or "00과" in memo:
                suspicious.append((path.name, memo.splitlines()[0]))

        findings = RuleEngine(Document.from_path(path)).run()
        required_by_topic: dict[str, set[str]] = {}
        for finding in findings:
            required_by_topic.setdefault(finding.topic, set()).add(finding.anchor_text)
        for topic, anchors in required_by_topic.items():
            covered = sum(1 for memo in active if topic in memo)
            required = 1 if topic in {"성취기준 코드 범위 표기", "성취기준 코드 표기 오류"} else len(anchors)
            if covered < required:
                missing.extend((path.name, topic, anchor) for anchor in sorted(anchors)[covered:required])

    print(f"files={len(files)} total_memos={total_memos} active_memos={active_memos}")
    print(f"missing={len(missing)} duplicates={len(duplicate_memos)} suspicious={len(suspicious)}")
    for item in missing:
        print("MISSING", *item, sep=" | ")
    for item in duplicate_memos:
        print("DUPLICATE", *item, sep=" | ")
    for item in suspicious:
        print("SUSPICIOUS", *item, sep=" | ")


if __name__ == "__main__":
    main()
