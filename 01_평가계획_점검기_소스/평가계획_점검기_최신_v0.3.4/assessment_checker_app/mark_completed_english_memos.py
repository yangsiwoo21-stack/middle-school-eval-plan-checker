from __future__ import annotations

from pathlib import Path
import argparse
import html
import re
import shutil
import tempfile
import zipfile


COMPLETION_NOTES = {
    "범교과 교육 누락": "수정 완료: 교통안전교육 및 실종·유괴의 예방·방지교육을 진도표에 반영함",
    "4번-6번 수행평가 영역명 불일치|어휘 활용 글쓰기": "수정 완료: 4번·6번 영역명을 '어휘 활용 글쓰기'로 일치시킴",
    "4번-6번 수행평가 영역명 불일치|세계 유명 건물 소개하기": "확인 완료: 띄어쓰기 차이를 제외하면 동일한 영역명으로 오류 없음",
    "월별 계획표에 없는 수행평가 성취기준": "확인 완료: [9영04-01]은 10월 3~4주 진도표와 수행평가 시기가 일치하여 오류 없음",
    "평가 결과 활용 과목명 누락": "수정 완료",
}


def add_strikeout_character_property(header: str) -> tuple[str, str]:
    existing = re.search(
        r'<hh:charPr\b(?=[^>]*\bid="(\d+)")[^>]*>[\s\S]*?<hh:strikeout shape="3D"[\s\S]*?</hh:charPr>',
        header,
    )
    if existing:
        return header, existing.group(1)

    properties = re.search(r'<hh:charProperties\b[^>]*\bitemCnt="(\d+)"[^>]*>[\s\S]*?</hh:charProperties>', header)
    base = re.search(r'<hh:charPr\b(?=[^>]*\bid="4")[^>]*>[\s\S]*?</hh:charPr>', header)
    if not properties or not base:
        raise RuntimeError("취소선 글자 속성을 만들 수 없습니다.")

    ids = [int(value) for value in re.findall(r'<hh:charPr\b[^>]*\bid="(\d+)"', properties.group())]
    new_id = str(max(ids, default=0) + 1)
    new_property = re.sub(r'\bid="4"', f'id="{new_id}"', base.group(), count=1)
    new_property = new_property.replace('<hh:strikeout shape="NONE"', '<hh:strikeout shape="3D"')
    updated_properties = properties.group().replace(
        f'itemCnt="{properties.group(1)}"', f'itemCnt="{int(properties.group(1)) + 1}"', 1
    ).replace("</hh:charProperties>", new_property + "</hh:charProperties>")
    return header[: properties.start()] + updated_properties + header[properties.end() :], new_id


def visible_text(xml: str) -> str:
    value = re.sub(r"<hp:lineBreak\s*/>", "\n", xml)
    return html.unescape("".join(re.findall(r"<hp:t(?:\s[^>]*)?>([\s\S]*?)</hp:t>", value)))


def completion_note(memo_text: str) -> str | None:
    for key, note in COMPLETION_NOTES.items():
        parts = key.split("|")
        if all(part in memo_text for part in parts):
            return note
    return None


def mark_completed_memos(section: str, strikeout_id: str) -> tuple[str, int]:
    pattern = re.compile(
        r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>'
    )
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        block = match.group()
        memo_text = visible_text(block)
        note = completion_note(memo_text)
        if not note:
            return block

        run = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="[^"]+"[^>]*><hp:t>([\s\S]*?)</hp:t></hp:run>', block)
        if not run:
            return block
        original = run.group(1)
        original = re.split(r'<hp:lineBreak\s*/>-\s*(?:수정|확인)\s*완료:', original, maxsplit=1)[0]
        replacement = (
            f'<hp:run charPrIDRef="{strikeout_id}"><hp:t>{original}</hp:t></hp:run>'
            f'<hp:run charPrIDRef="4"><hp:t><hp:lineBreak/>- {html.escape(note, quote=False)}</hp:t></hp:run>'
        )
        changed += 1
        return block[: run.start()] + replacement + block[run.end() :]

    return pattern.sub(replace, section), changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()

    if args.backup:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.file, args.backup)

    with zipfile.ZipFile(args.file, "r") as zin:
        entries = [(info, zin.read(info.filename)) for info in zin.infolist()]

    header = next(data.decode("utf-8") for info, data in entries if info.filename == "Contents/header.xml")
    header, strikeout_id = add_strikeout_character_property(header)
    changed = 0
    rewritten: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, data in entries:
        if info.filename == "Contents/header.xml":
            data = header.encode("utf-8")
        elif re.fullmatch(r"Contents/section\d+\.xml", info.filename):
            section, count = mark_completed_memos(data.decode("utf-8", errors="ignore"), strikeout_id)
            changed += count
            data = section.encode("utf-8")
        rewritten.append((info, data))

    with tempfile.NamedTemporaryFile(delete=False, suffix=".hwpx", dir=args.file.parent) as handle:
        temp = Path(handle.name)
    try:
        with zipfile.ZipFile(temp, "w") as zout:
            for info, data in rewritten:
                zout.writestr(info, data)
        with zipfile.ZipFile(temp, "r") as check:
            if bad := check.testzip():
                raise RuntimeError(f"손상된 HWPX 항목: {bad}")
        temp.replace(args.file)
    finally:
        temp.unlink(missing_ok=True)

    print(f"completed_memos={changed}")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
