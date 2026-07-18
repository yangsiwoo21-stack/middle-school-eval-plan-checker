from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from add_crosscurricular_memos import add_memo_preserving_existing
from checker import Document, ReviewFinding, RuleEngine
from final_review_audit import is_active_memo, memo_texts


TARGET_TOPICS = {
    "배점 오기입",
    "기본 양식 안내문 삭제",
    "불필요한 성취율 표 삭제",
}


def manual_findings(path: Path, document: Document) -> list[ReviewFinding]:
    if document.grade != 1 or document.subject != "보건":
        return []
    if "건강 안정과 응급 처치" not in document.text or "건강안전과 응급 처치" not in document.text:
        return []
    return [
        ReviewFinding(
            file_path=path,
            grade=document.grade,
            subject=document.subject,
            severity="상",
            topic="4번-6번 수행평가 영역명 불일치",
            anchor_text="건강 안정과 응급 처치",
            memo_text=(
                "◆ 4번-6번 수행평가 영역명 불일치\n"
                "- 4번: 건강 안정과 응급 처치\n"
                "- 6번: 건강안전과 응급 처치\n"
                "- 평가영역명을 동일하게 수정"
            ),
            context="4번 평가의 종류와 반영비율과 6번 수행평가 세부기준 대조",
        )
    ]


def finding_already_exists(finding: ReviewFinding, active_memos: list[str]) -> bool:
    normalized_anchor = "".join(finding.anchor_text.split())
    for memo in active_memos:
        if finding.topic not in memo:
            continue
        normalized_memo = "".join(memo.split())
        if normalized_anchor in normalized_memo or finding.memo_text == memo:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    review_files = [
        path
        for path in args.submission_root.rglob("*.hwpx")
        if path.parent.name == "1차 검토본" and "백업" not in str(path)
    ]
    backup_root = args.submission_root / "배점_양식_최종검수전_백업"
    changed: list[Path] = []
    failures: list[tuple[Path, str]] = []

    for path in sorted(review_files):
        document = Document.from_path(path)
        findings = [
            finding
            for finding in RuleEngine(document).run()
            if finding.topic in TARGET_TOPICS
        ]
        findings.extend(manual_findings(path, document))
        active = [memo for memo in memo_texts(path) if is_active_memo(memo)]
        pending = [finding for finding in findings if not finding_already_exists(finding, active)]
        if not pending:
            continue

        print(f"{document.grade}학년 {document.subject}: {len(pending)}건")
        for finding in pending:
            print(f"  - {finding.topic} | {finding.anchor_text}")

        if not args.apply:
            continue
        relative = path.relative_to(args.submission_root)
        backup = backup_root / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(path, backup)
        for finding in pending:
            if not add_memo_preserving_existing(path, finding):
                failures.append((path, finding.anchor_text))
        if not any(failure[0] == path for failure in failures):
            changed.append(path)

    print(f"검토 파일={len(review_files)} 변경 파일={len(changed)} 실패={len(failures)}")
    for path in changed:
        print(f"CHANGED | {path}")
    for path, anchor in failures:
        print(f"FAILED | {path} | {anchor}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
