from __future__ import annotations

from pathlib import Path
import argparse
import html
import re
import shutil
import tempfile
import zipfile

from add_crosscurricular_memos import add_memo_preserving_existing
from checker import (
    Document,
    ReviewFinding,
    achievement_first_appearance_lines,
    extract_achievement_codes,
    extract_visible_text,
)
from mark_completed_english_memos import add_strikeout_character_property


MEMO_PATTERN = re.compile(
    r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>'
)
RUN_PATTERN = re.compile(r'<hp:run\b[^>]*\bcharPrIDRef="[^"]+"[^>]*>[\s\S]*?</hp:run>')


def replace_runs(block: str, runs_xml: str) -> str:
    runs = list(RUN_PATTERN.finditer(block))
    if not runs:
        return block
    return block[: runs[0].start()] + runs_xml + block[runs[-1].end() :]


def plain_run(memo_text: str) -> str:
    content = html.escape(memo_text, quote=False).replace("\n", "<hp:lineBreak/>")
    return f'<hp:run charPrIDRef="4"><hp:t>{content}</hp:t></hp:run>'


def strike_and_status(block: str, strikeout_id: str, status: str) -> str:
    runs = list(RUN_PATTERN.finditer(block))
    if not runs:
        return block
    first = runs[0].group()
    text_match = re.search(r'<hp:t>([\s\S]*?)</hp:t>', first)
    if not text_match:
        return block
    original = re.split(
        r'<hp:lineBreak\s*/>\s*-?\s*(?:수정|확인)\s*완료',
        text_match.group(1),
        maxsplit=1,
    )[0]
    replacement = (
        f'<hp:run charPrIDRef="{strikeout_id}"><hp:t>{original}</hp:t></hp:run>'
        f'<hp:run charPrIDRef="4"><hp:t><hp:lineBreak/>{html.escape(status, quote=False)}</hp:t></hp:run>'
    )
    return replace_runs(block, replacement)


def timing_memo(document: Document, memo_text: str) -> str | None:
    if "평가시기-수행 성취기준 불일치" not in memo_text and "평가시기-성취기준 불일치" not in memo_text:
        return None
    period_match = re.search(r"평가시기\s*:?[ ]*(.+?)\s+월별", memo_text)
    if not period_match:
        period_match = re.search(r"평가시기\s*:\s*([^\n]+)", memo_text)
    if not period_match:
        return None
    period = period_match.group(1).strip()
    codes = extract_achievement_codes(memo_text)
    if not codes:
        return None
    lines = ["◆ 평가시기-성취기준 불일치", f"- 평가시기: {period}"]
    lines.extend(f"- {line}" for line in achievement_first_appearance_lines(codes, document.table_rows))
    lines.append("- 평가시기 또는 성취기준 수정")
    return "\n".join(lines)


def update_section(section: str, document: Document, strikeout_id: str) -> tuple[str, int]:
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        block = match.group()
        memo_text = extract_visible_text(block).strip()

        if "평가 결과 활용 과목명 누락" in memo_text:
            updated = strike_and_status(block, strikeout_id, "수정 완료")
        elif document.grade == 2 and document.subject == "역사" and "평가시기 표기 형식 확인" in memo_text:
            updated = strike_and_status(
                block,
                strikeout_id,
                "확인 완료: 평가시기 '9월 4주~11월 2주' 정상 표기",
            )
        elif refreshed := timing_memo(document, memo_text):
            updated = replace_runs(block, plain_run(refreshed))
        else:
            return block

        if updated != block:
            changed += 1
        return updated

    return MEMO_PATTERN.sub(replace, section), changed


def rewrite_file(path: Path, backup_root: Path) -> tuple[int, Document]:
    document = Document.from_path(path)
    with zipfile.ZipFile(path, "r") as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]

    header = next(data.decode("utf-8") for info, data in entries if info.filename == "Contents/header.xml")
    header, strikeout_id = add_strikeout_character_property(header)
    changed = 0
    rewritten: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, data in entries:
        if info.filename == "Contents/header.xml":
            data = header.encode("utf-8")
        elif re.fullmatch(r"Contents/section\d+\.xml", info.filename):
            section, count = update_section(data.decode("utf-8", errors="ignore"), document, strikeout_id)
            changed += count
            data = section.encode("utf-8")
        rewritten.append((info, data))

    if not changed:
        return 0, document

    backup = backup_root / path.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(path, backup)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".hwpx", dir=path.parent) as handle:
        temp = Path(handle.name)
    try:
        with zipfile.ZipFile(temp, "w") as archive:
            for info, data in rewritten:
                archive.writestr(info, data)
        with zipfile.ZipFile(temp, "r") as archive:
            if bad := archive.testzip():
                raise RuntimeError(f"손상된 HWPX 항목: {bad}")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return changed, document


def add_history_level_memo(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        section = "".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.startswith("Contents/section") and name.endswith(".xml")
        )
    if "성취기준·성취수준 미작성" in extract_visible_text(section):
        return False

    document = Document.from_path(path)
    finding = ReviewFinding(
        file_path=path,
        grade=2,
        subject="역사",
        severity="상",
        topic="성취기준·성취수준 미작성",
        anchor_text="성취기준별 성취수준",
        memo_text=(
            "◆ 성취기준·성취수준 미작성\n"
            "- 성취기준별 성취수준 표 미작성\n"
            "- 학기 단위 성취수준 표 미작성\n"
            "- 2022 개정 교육과정 자료 반영"
        ),
        context=document.sectors.get("achievement_level").text[:1200]
        if document.sectors.get("achievement_level")
        else document.text[:1200],
    )
    return add_memo_preserving_existing(path, finding)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    args = parser.parse_args()

    total = 0
    history_path: Path | None = None
    for path in sorted(args.submission_root.rglob("*_검토본.hwpx")):
        if path.parent.name != "1차 검토본" or any("백업" in part for part in path.parts):
            continue
        backup_root = path.parent.parent / "메모_표현_재정비전_백업"
        changed, document = rewrite_file(path, backup_root)
        if changed:
            total += changed
            print(f"{document.grade}학년 {document.subject}: {changed}개 정리")
        if document.grade == 2 and document.subject == "역사":
            history_path = path

    if history_path and add_history_level_memo(history_path):
        total += 1
        print("2학년 역사: 성취기준·성취수준 미작성 메모 추가")
    print(f"total={total}")


if __name__ == "__main__":
    main()
