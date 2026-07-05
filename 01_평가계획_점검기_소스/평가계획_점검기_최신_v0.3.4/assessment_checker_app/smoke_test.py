from pathlib import Path

from checker import Document, RuleEngine, split_combined_subject_document


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


if __name__ == "__main__":
    main()
