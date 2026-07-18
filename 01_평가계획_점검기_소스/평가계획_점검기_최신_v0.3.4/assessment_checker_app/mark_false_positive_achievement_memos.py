from __future__ import annotations

from pathlib import Path
import argparse
import html
import re
import shutil
import tempfile
import zipfile

from mark_completed_english_memos import add_strikeout_character_property, visible_text


MEMO_PATTERN = re.compile(
    r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>'
)


def confirmation_note(memo_text: str, grade: int, subject: str) -> str | None:
    if "4번-6번 성취기준 불일치" in memo_text:
        return "확인 완료: 동일 수행평가 영역끼리 대조하면 4번·6번 성취기준이 일치하여 오류 없음"
    if (
        grade == 2
        and subject == "국어"
        and "평가시기-수행평가 세부기준 성취기준 불일치" in memo_text
        and "복합양식 활용하여 글 쓰기" in memo_text
    ):
        return (
            "확인 완료: [9국01-06]은 '이해하기 쉽게 발표하기'의 성취기준이며 "
            "11월 3주 이전 월별 계획에서 확인되어 오류 없음"
        )
    return None


def update_section(section: str, strikeout_id: str, grade: int, subject: str) -> tuple[str, int]:
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        block = match.group()
        memo_text = visible_text(block)
        note = confirmation_note(memo_text, grade, subject)
        if not note or "확인 완료:" in memo_text:
            return block

        run = re.search(
            r'<hp:run\b[^>]*\bcharPrIDRef="[^"]+"[^>]*><hp:t>([\s\S]*?)</hp:t></hp:run>',
            block,
        )
        if not run:
            return block
        replacement = (
            f'<hp:run charPrIDRef="{strikeout_id}"><hp:t>{run.group(1)}</hp:t></hp:run>'
            f'<hp:run charPrIDRef="4"><hp:t><hp:lineBreak/>- {html.escape(note, quote=False)}</hp:t></hp:run>'
        )
        changed += 1
        return block[: run.start()] + replacement + block[run.end() :]

    return MEMO_PATTERN.sub(replace, section), changed


def update_file(path: Path, backup_root: Path, grade: int, subject: str) -> int:
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
            section, count = update_section(data.decode("utf-8", errors="ignore"), strikeout_id, grade, subject)
            changed += count
            data = section.encode("utf-8")
        rewritten.append((info, data))

    if not changed:
        return 0

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
    return changed


def infer_grade_subject(path: Path) -> tuple[int, str]:
    match = re.search(r"\((\d)학년\s+(.+?)과\)", path.name)
    if not match:
        return 0, ""
    return int(match.group(1)), match.group(2).replace("기술가정", "기술가정")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("submission_root", type=Path)
    args = parser.parse_args()

    total = 0
    for path in sorted(args.submission_root.rglob("*_검토본.hwpx")):
        if path.parent.name != "1차 검토본" or any("백업" in part for part in path.parts):
            continue
        grade, subject = infer_grade_subject(path)
        backup_root = path.parent.parent / "성취기준_메모_재점검전_백업"
        changed = update_file(path, backup_root, grade, subject)
        if changed:
            total += changed
            print(f"{grade}학년 {subject}: {changed}개 정리")
    print(f"total={total}")


if __name__ == "__main__":
    main()
