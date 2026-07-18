from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from checker import Document, HwpxMemoWriter, ReviewFinding, extract_visible_text


SUBJECT_ALIASES = {
    "기․가": "기술가정",
    "기·가": "기술가정",
    "기가": "기술가정",
    "진로": "진로와 직업",
}

TOPIC_ALIASES = {
    "실종·유괴의 예방·방지교육": ("실종", "유괴"),
    "장애인식개선교육": ("장애인식", "장애이해"),
    "디지털 시민교육": ("디지털시민", "디지털역량"),
    "청소년 노동인권교육": ("노동인권",),
    "약물중독 예방교육": ("약물중독", "약물의오용", "약물오용"),
    "사이버중독 예방교육": ("사이버중독", "스마트폰과의존", "도박예방"),
    "재난대비 안전교육": ("재난대비",),
    "건강한 식생활 및 영양교육": ("건강한식생활", "영양교육"),
    "학교폭력예방교육": ("학교폭력",),
    "다문화이해교육": ("다문화",),
    "교통안전교육": ("교통안전",),
    "생활안전교육": ("생활안전",),
    "진로교육": ("진로교육",),
    "독도교육": ("독도교육",),
    "통일교육": ("통일교육",),
    "아동학대 예방교육": ("아동학대",),
    "직업안전": ("직업안전",),
    "응급처치교육": ("응급처치",),
    "보건교육": ("보건교육",),
    "성교육": ("성교육",),
    "인권교육": ("인권교육",),
}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def canonical_topic(text: str) -> str:
    value = re.sub(r"\s*\([^)]*\)", "", text).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def parse_hours(text: str) -> int | None:
    match = re.search(r"\d+", compact(text))
    return int(match.group()) if match else None


def extract_second_semester_assignments(path: Path) -> dict[tuple[int, str], dict[str, int]]:
    rows = Document.from_path(path).table_rows
    header_indexes = [
        index
        for index, row in enumerate(rows)
        if "학기" in row and "국어" in row and any(cell in row for cell in ("창체", "영역별 총시수"))
    ]
    if len(header_indexes) < 6:
        raise RuntimeError(f"학년별 범교과 배정표 머리글을 찾지 못했습니다: {path}")

    assignments: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for block_no, header_index in enumerate(header_indexes[:6]):
        grade = block_no % 3 + 1
        header = rows[header_index]
        semester_col = header.index("학기")
        subject_columns: list[tuple[int, str]] = []
        for col in range(semester_col + 1, len(header)):
            raw_subject = header[col].strip()
            if raw_subject in {"창체", "영역별 총시수"}:
                break
            if not raw_subject or raw_subject == "교 과":
                continue
            subject_columns.append((col, SUBJECT_ALIASES.get(raw_subject, raw_subject)))

        end = header_indexes[block_no + 1] if block_no + 1 < len(header_indexes) else len(rows)
        for row in rows[header_index + 1 : end]:
            if semester_col >= len(row) or compact(row[semester_col]) != "2학기":
                continue
            topic = canonical_topic(row[semester_col - 2])
            if not topic:
                continue
            for col, subject in subject_columns:
                hours = parse_hours(row[col]) if col < len(row) else None
                if hours:
                    assignments[(grade, subject)][topic] = hours
    return dict(assignments)


def monthly_plan_text(document: Document) -> str:
    sector = document.sectors.get("monthly_plan")
    if sector:
        return sector.text
    cut_points = [
        position
        for marker in ("평가의 목적", "평가의 기본 방향", "성취기준 및 성취수준")
        if (position := document.text.find(marker)) > 0
    ]
    return document.text[: min(cut_points)] if cut_points else document.text


def recorded_hours(topic: str, text: str) -> int:
    haystack = compact(text)
    aliases = TOPIC_ALIASES.get(topic, (topic,))
    matches: list[tuple[int, int]] = []
    for alias in aliases:
        needle = compact(alias)
        for occurrence in re.finditer(re.escape(needle), haystack):
            nearby = haystack[occurrence.end() : occurrence.end() + 40]
            hour_match = re.search(r"(\d+)시간", nearby)
            if hour_match:
                matches.append((occurrence.start(), int(hour_match.group(1))))

    # Multiple aliases can point at the same phrase. Count each source occurrence once.
    unique: dict[int, int] = {}
    for position, hours in matches:
        if not any(abs(position - saved) <= 20 for saved in unique):
            unique[position] = hours
    return sum(unique.values())


def memo_anchor(document: Document) -> str:
    for candidate in ("수업 방법", "수업방법", "교수·학습 방법", "교수학습 방법"):
        if candidate in document.text:
            return candidate
    return "단원명"


def build_finding(path: Path, document: Document, missing: dict[str, int]) -> ReviewFinding:
    lines = [f"{topic} ({hours}시간)" for topic, hours in missing.items()]
    memo = "◆ 범교과 교육 누락\n- " + "\n- ".join(lines)
    memo += "\n- 진도표 <수업 방법> 칸에 교육명과 시간을 반영해 주세요."
    return ReviewFinding(
        file_path=path,
        grade=document.grade,
        subject=document.subject,
        severity="상",
        topic="범교과 교육 누락",
        anchor_text=memo_anchor(document),
        memo_text=memo,
        context=monthly_plan_text(document)[:4000],
    )


def add_memo_preserving_existing(path: Path, finding: ReviewFinding) -> bool:
    with zipfile.ZipFile(path, "r") as zin:
        entries = [(info, zin.read(info.filename)) for info in zin.infolist()]

    section_texts = {
        info.filename: data.decode("utf-8", errors="ignore")
        for info, data in entries
        if re.fullmatch(r"Contents/section\d+\.xml", info.filename)
    }
    next_number = max(
        (HwpxMemoWriter.next_memo_number(section) for section in section_texts.values()),
        default=1,
    )
    inserted = False
    new_entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, data in entries:
        if info.filename == "Contents/header.xml":
            data = HwpxMemoWriter.ensure_memo_properties(data.decode("utf-8")).encode("utf-8")
        elif info.filename in section_texts and not inserted:
            section, inserted = HwpxMemoWriter.insert_memo_near_anchor(
                section_texts[info.filename], finding, next_number
            )
            data = section.encode("utf-8")
        new_entries.append((info, data))

    if not inserted:
        return False

    with tempfile.NamedTemporaryFile(delete=False, suffix=".hwpx", dir=path.parent) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w") as zout:
            for info, data in new_entries:
                zout.writestr(info, data)
        with zipfile.ZipFile(temp_path, "r") as check:
            bad = check.testzip()
            if bad:
                raise RuntimeError(f"손상된 HWPX 항목: {bad}")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="2학기 범교과 누락 메모를 1차 검토본 진도표에 추가합니다.")
    parser.add_argument("submission_root", type=Path)
    parser.add_argument("assignment_hwpx", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    assignments = extract_second_semester_assignments(args.assignment_hwpx)
    review_files = [path for path in args.submission_root.rglob("*.hwpx") if path.parent.name == "1차 검토본"]
    results: list[tuple[Path, int | None, str | None, dict[str, int], str]] = []
    backup_root = args.submission_root / "범교과_메모_추가전_백업"

    for path in sorted(review_files):
        document = Document.from_path(path)
        expected = assignments.get((document.grade, document.subject or ""), {})
        plan_text = monthly_plan_text(document)
        missing = {
            topic: hours
            for topic, hours in expected.items()
            if recorded_hours(topic, plan_text) < hours
        }
        status = "누락 없음"
        if missing:
            status = "점검만"
            if args.apply:
                relative = path.relative_to(args.submission_root)
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    shutil.copy2(path, backup)
                finding = build_finding(path, document, missing)
                status = "메모 추가" if add_memo_preserving_existing(path, finding) else "메모 위치 실패"
        results.append((path, document.grade, document.subject, missing, status))

    for path, grade, subject, missing, status in results:
        details = ", ".join(f"{topic}({hours}시간)" for topic, hours in missing.items()) or "없음"
        print(f"{grade}학년 {subject}: {status} | {details} | {path.name}")

    failures = [item for item in results if item[-1] == "메모 위치 실패"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
