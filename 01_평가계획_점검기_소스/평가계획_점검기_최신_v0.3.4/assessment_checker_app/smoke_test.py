from pathlib import Path

from checker import (
    Document,
    HwpxMemoWriter,
    RuleEngine,
    achievement_first_appearance_lines,
    assessment_overview_items_from_rows,
    comparable_interval_scores,
    extract_achievement_codes,
    is_consulting_period_format,
    is_valid_performance_area_name,
    learned_codes_before_or_by_period,
    performance_area_names_from_ratio_rows,
    performance_detail_blocks_from_tables,
    periods_from_overview_text,
    split_combined_subject_document,
)


def main() -> None:
    sample_text = """
2026학년도 1학기 1학년 국어과 교수 · 학습 및 평가 운영 계획
2022 개정 교육과정
성취기준별 성취수준
학기 단위 성취수준
4. 평가의 종류와 반영비율
수행평가 100%
6. 수행평가
평가영역명 읽기 활동(30점)
기본점수 9점
""" + ("국어 문서 본문입니다. " * 120) + """

2026학년도 1학기 1학년 수학과 교수 · 학습 및 평가 운영 계획
2022 개정 교육과정
성취기준별 성취수준
학기 단위 성취수준
4. 평가의 종류와 반영비율
수행평가 100%
6. 수행평가
평가영역명 문제 해결(30점)
기본점수 9점
""" + ("수학 문서 본문입니다. " * 120) + """
""".strip()
    document = Document(Path("2026학년도 1학기 1학년 합본.hwpx"), sample_text, None)
    parts = split_combined_subject_document(document)
    findings = []
    for part in parts:
        findings.extend(RuleEngine(part).run())

    print("split_parts", len(parts), [part.subject for part in parts])
    print("findings", len(findings))

    overview_rows = [
        ["평가 종류", "정기시험", "정기시험", "수행평가", "수행평가", "합계"],
        ["시기/영역", "1차", "1차", "9월 1주", "11월 2주", ""],
        ["시기/영역", "1차", "1차", "진로 이해", "직업 탐색", ""],
        ["영역 만점 (반영비율)", "90점 (45%)", "10점 (5%)", "20점 (20%)", "30점 (30%)", "100%"],
        ["성취기준", "[9국01-01]", "[9국01-01]", "[9진로01-01]", "[9진로02-01]", ""],
        ["평가 시기", "10월 2주", "10월 2주", "9월 1주", "11월 2주", ""],
    ]
    assert performance_area_names_from_ratio_rows(overview_rows) == ["진로 이해", "직업 탐색"]
    overview_items = assessment_overview_items_from_rows(overview_rows)
    assert [(item.name, item.score, item.ratio) for item in overview_items] == [
        ("진로 이해", 20, 20.0),
        ("직업 탐색", 30, 30.0),
    ]
    assert overview_items[0].achievement_codes == ("[9진로01-01]",)

    detail_rows = [["평가 영역명", "발표하기", "영역 만점", "", "학기", "2학기"]]
    assert performance_detail_blocks_from_tables(detail_rows)[0]["score"] == 0
    merged_detail_rows = [
        ["평가 영역명", "평가 영역명", "세계 유명 건물 소개하기", "세계 유명 건물 소개하기", "영역 만점", "20", "학기", "2학기"],
        ["교육과정 성취기준", "[9영04-01]"],
        ["평가 요소", "관련 성취기준 및 평가 방법"],
        ["성취기준", "[9영04-06]"],
    ]
    merged_blocks = performance_detail_blocks_from_tables(merged_detail_rows)
    assert merged_blocks[0]["name"] == "세계 유명 건물 소개하기"
    assert "[9영04-06]" not in " ".join(" ".join(row) for row in merged_blocks[0]["rows"])
    assert comparable_interval_scores([30, 28, 26, 26, 24, 22]) == [30, 28, 26, 24, 22]
    assert is_consulting_period_format("9월 4~5주")
    assert is_consulting_period_format("9월 2주, 10월 3주, 11월 4~5주")
    assert is_consulting_period_format("수시")
    assert not is_valid_performance_area_name("정기시험(2차)")
    assert not is_consulting_period_format("9월 4주~11월")
    assert extract_achievement_codes("[09음01-01] [ 9음02-02]") == [
        "[9음01-01]",
        "[9음02-02]",
    ]
    merged_month_rows = [
        ["9", "2-3", "단원", "[9수03-03] 삼각형을 작도한다."],
        ["10", "1", "단원", "[9수03-06] 부채꼴을 이해한다."],
    ]
    assert "[9수03-03]" in learned_codes_before_or_by_period("", "10월 1주", merged_month_rows)
    assert achievement_first_appearance_lines(["[9수03-06]"], merged_month_rows) == [
        "[9수03-06] 최초 등장: 10월 1주"
    ]
    split_period_text = "정기시험\n평가 시기\n12월 2주\n9월 2주~10월 3주\n9월 4주~11월\n2주"
    assert periods_from_overview_text(split_period_text, 2)[-1] == "9월 4주~11월 2주"

    timing_rows = [
        ["9", "1", "단원", "[9국03-07] 복합양식을 활용한다."],
        ["11", "3", "단원", "[9국01-06] 발표 내용을 구성한다."],
        ["평가 종류", "수행평가", "수행평가"],
        ["시기/영역", "복합양식 활용하여 글 쓰기", "이해하기 쉽게 발표하기"],
        ["영역 만점 (반영비율)", "20점 (20%)", "10점 (10%)"],
        ["성취기준", "[9국03-07]", "[9국01-06]"],
        ["평가 시기", "11월 1주", "11월 3주"],
        ["평가 영역명", "복합양식 활용하여 글 쓰기(20점)"],
        ["교육과정 성취기준", "[9국03-07]"],
        ["평가 영역명", "이해하기 쉽게 발표하기(10점)"],
        ["교육과정 성취기준", "[9국01-06]"],
    ]
    timing_document = Document(
        Path("2026학년도 2학기 2학년 국어과.hwpx"),
        (
            "1. 국어과 교수·학습 운영 계획\n[9국03-07]\n[9국01-06]\n"
            "4. 평가의 종류와 반영비율\n"
            "6. 수행평가 세부기준\n복합양식 활용하여 글 쓰기 [9국03-07]\n"
            "이해하기 쉽게 발표하기 [9국01-06]\n7. 정의적 능력 평가"
        ),
        None,
        grade=2,
        subject="국어",
        table_rows=timing_rows,
    )
    assert not any(
        finding.topic == "평가시기-성취기준 불일치"
        for finding in RuleEngine(timing_document).run()
    )

    unmatched_detail = Document(
        Path("2026학년도 2학기 1학년 수학과.hwpx"),
        (
            "1. 수학과 교수·학습 운영 계획\n[9수03-01]\n"
            "4. 평가의 종류와 반영비율\n기하학 논술 10월 1주\n"
            "6. 수행평가 세부기준\n삼각형 작도하기 [9수03-01]\n7. 정의적 능력 평가"
        ),
        None,
        grade=1,
        subject="수학",
        table_rows=[
            ["9", "1", "단원", "[9수03-01] 삼각형을 작도한다."],
            ["평가 종류", "수행평가"],
            ["시기/영역", "기하학 논술"],
            ["영역 만점 (반영비율)", "20점 (20%)"],
            ["평가 시기", "10월 1주"],
            ["평가 영역명", "삼각형 작도하기(20점)"],
            ["교육과정 성취기준", "[9수03-01]"],
        ],
    )
    unmatched_findings = RuleEngine(unmatched_detail).run()
    assert not any(f.topic == "수행평가 평가시기 확인 필요" for f in unmatched_findings)
    assert any(f.topic == "4번-6번 수행평가 영역명 불일치" for f in unmatched_findings)
    assert not any(f.topic.startswith("엑셀형 관계검증: 수행평가 영역 매칭") for f in unmatched_findings)

    code_mismatch = Document(
        Path("2026학년도 2학기 3학년 과학과.hwpx"),
        (
            "1. 과학과 교수·학습 운영 계획\n[9과21-01]\n[9과21-02]\n"
            "4. 평가의 종류와 반영비율\n과학적 사고하기 수시 [9과21-01]\n"
            "6. 수행평가 세부기준\n과학적 사고하기 [9과21-01] [9과21-02]\n"
            "7. 정의적 능력 평가"
        ),
        None,
        grade=3,
        subject="과학",
        table_rows=[
            ["9", "1", "단원", "[9과21-01] 세포 분열"],
            ["9", "2", "단원", "[9과21-02] 염색체"],
            ["평가 종류", "수행평가"],
            ["시기/영역", "과학적 사고하기"],
            ["영역 만점 (반영비율)", "20점 (20%)"],
            ["성취기준", "[9과21-01]"],
            ["평가 시기", "수시"],
            ["평가 영역명", "과학적 사고하기(20점)"],
            ["교육과정 성취기준", "[9과21-01] [9과21-02]"],
        ],
    )
    assert any(
        f.topic == "4번-6번 수행평가 성취기준 불일치"
        for f in RuleEngine(code_mismatch).run()
    )

    empty_levels = Document(
        Path("2026학년도 2학기 2학년 역사과.hwpx"),
        (
            "3. 성취기준 및 성취수준\n가. 성취기준별 성취수준\n"
            "<2022 개정 교육과정 성취기준별 성취기준 표 붙여넣기>\n"
            "나. 학기 단위 성취수준\n<2022 개정 교육과정 학기단위 성취수준 표 붙여넣기>\n"
            "4. 평가의 종류와 반영비율"
        ),
        None,
        grade=2,
        subject="역사",
    )
    level_findings = RuleEngine(empty_levels).run()
    assert any(finding.topic == "성취기준·성취수준 미작성" for finding in level_findings)

    inverted_score = Document(
        Path("2026학년도 2학기 1학년 보건과.hwpx"),
        "6. 수행평가 세부기준",
        None,
        grade=1,
        subject="보건",
        table_rows=[
            ["평가 영역명", "응급 처치(100점)"],
            ["평가 요소", "채점 기준", "배점"],
            ["심폐소생술 시행하기", "순서를 정확히 시행한다.", "30"],
            ["심폐소생술 시행하기", "1~2가지 순서를 틀린다.", "82"],
            ["심폐소생술 시행하기", "3가지 순서를 틀린다.", "26"],
            ["기본점수", "10"],
        ],
    )
    inversion_findings = RuleEngine(inverted_score).run()
    assert any(finding.topic == "배점 오기입" and finding.anchor_text == "1~2가지 순서를 틀린다." for finding in inversion_findings)

    template_leftover = Document(
        Path("2026학년도 2학기 2학년 역사과.hwpx"),
        (
            "5. 성취율과 성취도\n"
            "해당 교과에 맞는 성취율과 성취도를 제시\n"
            "체육·예술(음악·미술) 교과의 과목 성취도는 다음과 같이 평정합니다."
        ),
        None,
        grade=2,
        subject="역사",
    )
    leftover_findings = RuleEngine(template_leftover).run()
    assert any(finding.topic == "불필요한 성취율 표 삭제" for finding in leftover_findings)

    malformed = Document(
        Path("2026학년도 2학기 1학년 음악과.hwpx"),
        "1. 음악과 교수·학습 운영 계획\n2022 개정 교육과정 [09음01-01]",
        None,
    )
    malformed_findings = RuleEngine(malformed).run()
    assert any(f.topic == "성취기준 코드 표기 오류" for f in malformed_findings)

    missing_subject = Document(
        Path("2026학년도 2학기 1학년 국어과.hwpx"),
        "10. 평가 결과 분석 및 활용\n평가 결과는 가정과 연계하여 00과 핵심역량 향상 자료로 활용한다.",
        None,
        grade=1,
        subject="국어",
    )
    missing_subject_findings = RuleEngine(missing_subject).run()
    assert any(f.topic == "평가 결과 활용 과목명 누락" for f in missing_subject_findings)

    named_subject = Document(
        Path("2026학년도 2학기 1학년 국어과.hwpx"),
        "10. 평가 결과 분석 및 활용\n평가 결과는 가정과 연계하여 국어과 핵심역량 향상 자료로 활용한다.",
        None,
        grade=1,
        subject="국어",
    )
    assert not any(f.topic == "평가 결과 활용 과목명 누락" for f in RuleEngine(named_subject).run())

    memo_xml = (
        '<hp:run><hp:ctrl><hp:fieldBegin id="100" type="MEMO">'
        '<hp:subList><hp:p><hp:run><hp:t>old memo</hp:t></hp:run></hp:p></hp:subList>'
        '</hp:fieldBegin></hp:ctrl><hp:t>본문</hp:t>'
        '<hp:ctrl><hp:fieldEnd beginIDRef="100"/></hp:ctrl></hp:run>'
    )
    stripped = HwpxMemoWriter.strip_existing_memos(memo_xml)
    assert 'type="MEMO"' not in stripped and "fieldEnd" not in stripped and "본문" in stripped
    print("regression_checks", "ok")


if __name__ == "__main__":
    main()
