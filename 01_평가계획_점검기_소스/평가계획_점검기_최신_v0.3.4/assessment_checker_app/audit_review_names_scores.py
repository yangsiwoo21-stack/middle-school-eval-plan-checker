from __future__ import annotations

from pathlib import Path
import argparse

from checker import (
    Document,
    assessment_overview_items,
    is_valid_performance_area_name,
    match_overview_item_by_name,
    match_relation_detail_by_name,
    normalize_area_name,
    performance_detail_relation_items,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    args = parser.parse_args()

    files = [
        path
        for path in args.submission_root.rglob("*_검토본.hwpx")
        if path.parent.name == "1차 검토본" and "백업" not in str(path)
    ]
    issue_count = 0
    for path in sorted(files):
        document = Document.from_path(path)
        overview = [item for item in assessment_overview_items(document) if is_valid_performance_area_name(item.name)]

        detail = []
        seen: set[tuple[str, object]] = set()
        for item in performance_detail_relation_items(document):
            key = (normalize_area_name(str(item["name"])), item["score"])
            if key in seen:
                continue
            seen.add(key)
            detail.append(item)

        overview_by_name = {normalize_area_name(item.name): item for item in overview}
        detail_by_name = {normalize_area_name(str(item["name"])): item for item in detail}
        issues: list[str] = []
        for item in overview:
            matched = match_relation_detail_by_name(item.name, detail_by_name)
            if not matched:
                issues.append(f"4번에만 있는 영역: {item.name}")
            elif item.score is not None and matched["score"] != item.score:
                issues.append(f"만점 불일치 {item.name}: 4번 {item.score}점 / 6번 {matched['score']}점")
        for item in detail:
            name = str(item["name"])
            if not match_overview_item_by_name(name, overview_by_name):
                issues.append(f"6번에만 있는 영역: {name}")

        print(f"\nFILE {path.name}")
        print("  4번:", [(item.name, item.score, item.ratio) for item in overview])
        print("  6번:", [(str(item["name"]), item["score"]) for item in detail])
        print("  결과:", issues or ["평가명·만점 일치"])
        issue_count += len(issues)

    print(f"\nfiles={len(files)} issues={issue_count}")


if __name__ == "__main__":
    main()
