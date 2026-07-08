from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import html
import json
import re
import shutil
import zipfile


CODE_RE = re.compile(r"\[[0-9][^\]\s]{1,20}\]")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
POINT_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*점\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")

SUBJECTS = [
    "기술가정",
    "기술과",
    "국어",
    "도덕",
    "사회",
    "역사",
    "수학",
    "과학",
    "체육",
    "음악",
    "미술",
    "영어",
    "한문",
    "정보",
]


@dataclass
class ReviewFinding:
    file_path: Path
    grade: int | None
    subject: str | None
    severity: str
    topic: str
    anchor_text: str
    memo_text: str
    context: str

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "file_path": str(self.file_path),
            "grade": self.grade,
            "subject": self.subject,
            "severity": self.severity,
            "topic": self.topic,
            "anchor_text": self.anchor_text,
            "memo_text": self.memo_text,
            "context": self.context,
        }


@dataclass
class StageReviewResult:
    file_path: Path
    grade: int | None
    subject: str | None
    stage_no: int
    stage_name: str
    status: str
    message: str
    finding_count: int = 0

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "file_path": str(self.file_path),
            "grade": self.grade,
            "subject": self.subject,
            "stage_no": self.stage_no,
            "stage_name": self.stage_name,
            "status": self.status,
            "message": self.message,
            "finding_count": self.finding_count,
        }


@dataclass(frozen=True)
class GradePolicy:
    grade: int | None
    curriculum: str
    is_grade1: bool
    is_free_semester: bool
    skip_regular_exam_checks: bool
    require_2022_achievement_levels: bool


def grade_policy_for(document: "Document") -> GradePolicy:
    grade = document.grade
    is_grade1 = grade == 1
    is_free_semester = is_grade1 and "자유학기" in document.text
    return GradePolicy(
        grade=grade,
        curriculum="2022 개정" if grade in {1, 2} else ("2015 개정" if grade == 3 else "확인 필요"),
        is_grade1=is_grade1,
        is_free_semester=is_free_semester,
        skip_regular_exam_checks=is_free_semester,
        require_2022_achievement_levels=grade in {1, 2},
    )


class ReviewRunner:
    def review_file(self, path: Path) -> list[ReviewFinding]:
        document = Document.from_path(path)
        findings: list[ReviewFinding] = []
        for review_document in split_combined_subject_document(document):
            findings.extend(RuleEngine(review_document).run())
        return dedupe_findings(findings)

    def review_file_with_stages(self, path: Path) -> tuple[list[ReviewFinding], list[StageReviewResult]]:
        document = Document.from_path(path)
        findings: list[ReviewFinding] = []
        stages: list[StageReviewResult] = []
        for review_document in split_combined_subject_document(document):
            document_findings = RuleEngine(review_document).run()
            findings.extend(document_findings)
            stages.extend(build_stage_results(review_document, document_findings))
        return dedupe_findings(findings), stages

    def review_folder(self, folder: Path) -> list[ReviewFinding]:
        files = self._discover_documents(folder)
        findings: list[ReviewFinding] = []
        for path in files:
            try:
                findings.extend(self.review_file(path))
            except Exception:
                continue
        return dedupe_findings(findings)

    def review_folder_with_stages(self, folder: Path) -> tuple[list[ReviewFinding], list[StageReviewResult]]:
        files = self._discover_documents(folder)
        findings: list[ReviewFinding] = []
        stages: list[StageReviewResult] = []
        for path in files:
            try:
                file_findings, file_stages = self.review_file_with_stages(path)
            except Exception:
                continue
            findings.extend(file_findings)
            stages.extend(file_stages)
        return dedupe_findings(findings), stages

    def create_memo_copies(self, findings: list[ReviewFinding], out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        by_file: dict[Path, list[ReviewFinding]] = {}
        for finding in findings:
            if finding.file_path.suffix.lower() == ".hwpx":
                by_file.setdefault(finding.file_path, []).append(finding)
        summary = []
        for src, file_findings in by_file.items():
            dst = out_dir / f"{src.stem}_메모첨부{src.suffix}"
            try:
                result = HwpxMemoWriter.copy_with_memos(src, dst, file_findings)
            except PermissionError:
                dst = next_available_path(out_dir / f"{src.stem}_메모첨부_새로생성{src.suffix}")
                result = HwpxMemoWriter.copy_with_memos(src, dst, file_findings)
            summary.append(result)
        (out_dir / "assessment_memo_creation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _discover_documents(self, folder: Path) -> list[Path]:
        paths: list[Path] = []
        for suffix in ("*.hwpx", "*.md"):
            paths.extend(folder.rglob(suffix))
        paths = [path for path in paths if not is_generated_review_file(path)]
        # Prefer HWPX when matching MD exists.
        hwpx_stems = {p.with_suffix("").name for p in paths if p.suffix.lower() == ".hwpx"}
        filtered = []
        for path in paths:
            if path.suffix.lower() == ".md" and path.with_suffix("").name in hwpx_stems:
                continue
            filtered.append(path)
        return sorted(filtered)


def is_generated_review_file(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    name = path.name
    return (
        "memo_copies" in parts
        or "assessment_checker_output" in parts
        or "outputs" in parts
        or "output" in parts
        or "_메모첨부" in name
        or "_memo" in name.lower()
        or name == "memo_creation_summary.json"
    )


class Document:
    def __init__(
        self,
        path: Path,
        text: str,
        section_xml: str | None = None,
        *,
        grade: int | None = None,
        subject: str | None = None,
        table_rows: list[list[str]] | None = None,
    ) -> None:
        self.path = path
        self.text = normalize_text(text)
        self.section_xml = section_xml
        self.table_rows = table_rows if table_rows is not None else (extract_hwpx_table_rows(section_xml) if section_xml else [])
        self.grade = grade if grade is not None else infer_grade(path, self.text)
        self.subject = subject if subject is not None else infer_subject(path, self.text)
        self.sectors = split_standard_sectors(self.text)

    @classmethod
    def from_path(cls, path: Path) -> "Document":
        if path.suffix.lower() == ".hwpx":
            section_xml = read_hwpx_section(path)
            section_xml = HwpxMemoWriter.strip_existing_memos(section_xml)
            text = extract_visible_text(section_xml)
            return cls(path, text, section_xml)
        return cls(path, read_text_file(path), None)

    def around(self, needle: str, radius: int = 500) -> str:
        idx = self.text.find(needle)
        if idx < 0:
            return self.text[: radius * 2]
        return self.text[max(0, idx - radius) : idx + len(needle) + radius]


@dataclass
class SubjectSectionMarker:
    start: int
    subject: str
    header: str


@dataclass(frozen=True)
class StandardSector:
    sector_id: str
    title: str
    start: int
    end: int
    text: str
    confidence: str


@dataclass(frozen=True)
class AssessmentOverviewItem:
    name: str
    ratio: float | None
    score: int | None
    achievement_codes: tuple[str, ...]
    period: str
    source: str
    confidence: str


@dataclass(frozen=True)
class RelationAuditRow:
    compare_area: str
    check_item: str
    key: str
    left_value: str
    right_value: str
    status: str
    opinion: str
    severity: str
    context: str


def split_combined_subject_document(document: Document) -> list[Document]:
    markers = find_subject_section_markers(document.text)
    if len(markers) < 2:
        return [document]

    documents: list[Document] = []
    if markers[0].start > 1000:
        prefix = document.text[: markers[0].start].strip()
        if len(prefix) >= 500:
            prefix_subject = infer_subject(document.path, prefix)
            if prefix_subject is None and "[9국" in prefix:
                prefix_subject = "국어"
            documents.append(
                Document(
                    document.path,
                    prefix,
                    None,
                    grade=infer_grade(document.path, prefix) or document.grade,
                    subject=prefix_subject,
                    table_rows=table_rows_for_subject_chunk(document.table_rows, prefix),
                )
            )
    for index, marker in enumerate(markers):
        end = markers[index + 1].start if index + 1 < len(markers) else len(document.text)
        chunk = document.text[marker.start:end].strip()
        if len(chunk) < 500:
            continue
        documents.append(
            Document(
                document.path,
                chunk,
                None,
                grade=infer_grade(document.path, chunk) or document.grade,
                subject=marker.subject,
                table_rows=table_rows_for_subject_chunk(document.table_rows, chunk),
            )
        )
    return documents or [document]


def table_rows_for_subject_chunk(rows: list[list[str]], chunk: str) -> list[list[str]]:
    """Keep table rows that belong to a virtual subject split from a combined file."""
    if not rows:
        return []

    compact_chunk = re.sub(r"\s+", "", chunk)
    chunk_codes = set(CODE_RE.findall(chunk))
    generic_labels = {
        "평가영역명",
        "교육과정성취기준",
        "성취기준",
        "평가기준",
        "평가방법",
        "평가요소",
        "채점기준",
        "배점",
        "영역만점",
        "기본점수",
        "평가시기",
        "반영비율",
        "수행평가",
        "정기시험",
    }

    matched: list[list[str]] = []
    for row in rows:
        cells = [clean_cell(cell) for cell in row if clean_cell(cell)]
        row_text = clean_cell(" ".join(cells))
        compact_row = re.sub(r"\s+", "", row_text)
        if not compact_row:
            continue
        row_codes = set(CODE_RE.findall(row_text))
        if row_codes and chunk_codes and not (row_codes & chunk_codes):
            continue
        if len(compact_row) >= 12 and compact_row in compact_chunk:
            matched.append(row)
            continue
        distinctive = [
            re.sub(r"\s+", "", cell)
            for cell in cells
            if len(re.sub(r"\s+", "", cell)) >= 5
            and cell not in generic_labels
            and not is_subject_split_generic_cell(cell)
        ]
        hits = sum(1 for cell in distinctive[:6] if cell and cell in compact_chunk)
        if hits >= 2 or (row_codes and row_codes <= chunk_codes and hits >= 1):
            matched.append(row)

    if not matched:
        return []

    scored: list[tuple[int, int, list[str]]] = []
    for order, row in enumerate(matched):
        row_text = clean_cell(" ".join(row))
        compact_row = re.sub(r"\s+", "", row_text)
        if not compact_row:
            continue
        row_codes = set(CODE_RE.findall(row_text))
        if row_codes and chunk_codes and not (row_codes & chunk_codes):
            continue
        score = 0
        for token in context_tokens(row_text):
            token_compact = re.sub(r"\s+", "", token)
            if token_compact and token_compact in compact_chunk:
                score += 2 if CODE_RE.fullmatch(token) else 1
        if compact_row in compact_chunk:
            score += 4
        if score > 0:
            scored.append((order, score, row))

    if not scored:
        return matched
    # Preserve document order while filtering rows that only matched on generic labels.
    return [row for _, score, row in scored if score >= 2] or [row for _, _, row in scored]


def is_subject_split_generic_cell(cell: str) -> bool:
    compact = re.sub(r"\s+", "", clean_cell(cell))
    if not compact:
        return True
    return bool(
        re.search(r"평가요소중어느것도만족하지않지만유사내용을수행한경우", compact)
        or re.search(r"채점기준을?\d+개만족하는경우", compact)
        or re.search(r"채점기준을모두만족하는경우", compact)
        or re.search(r"본인의의사에의한미응시", compact)
        or re.search(r"장기미인정결석", compact)
    )


def find_subject_section_markers(text: str) -> list[SubjectSectionMarker]:
    candidates: list[SubjectSectionMarker] = []
    aliases = subject_aliases()
    for alias, subject in aliases:
        strong_pattern = re.compile(
            rf"(?P<header>(?:^|\n)\s*20\d{{2}}\s*학년도\s*\d\s*학기\s*\d\s*학년\s*{re.escape(alias)}\s*교수\s*[·ㆍ∙]?\s*학습\s*및\s*평가\s*(?:운영\s*)?계획[^\n]*)"
        )
        for match in strong_pattern.finditer(text):
            header = clean_cell(match.group("header"))
            if "예시" in header or "유의사항" in header:
                continue
            candidates.append(SubjectSectionMarker(match.start("header"), subject, header))

    for alias, subject in aliases:
        pattern = re.compile(
            rf"(?P<header>[^\n]{{0,90}}{re.escape(alias)}[^\n]{{0,90}}(?:교수\s*[·ㆍ∙]?\s*학습|교수학습|평가\s*세부\s*계획|평가운영계획|평가\s*운영\s*계획)[^\n]{{0,90}})"
        )
        for match in pattern.finditer(text):
            header = clean_cell(match.group("header"))
            if "예시" in header or "유의사항" in header:
                continue
            candidates.append(SubjectSectionMarker(match.start("header"), subject, header))

    candidates.sort(key=lambda item: item.start)
    markers: list[SubjectSectionMarker] = []
    for candidate in candidates:
        if markers:
            previous = markers[-1]
            if candidate.subject == previous.subject:
                continue
            if candidate.start - previous.start < 800:
                continue
        markers.append(candidate)
    return markers


def subject_aliases() -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = [
        ("기술·가정과", "기술가정"),
        ("기술ㆍ가정과", "기술가정"),
        ("기술 가정과", "기술가정"),
        ("기술가정과", "기술가정"),
        ("기술과", "기술가정"),
    ]
    for subject in SUBJECTS:
        if subject in {"기술가정", "기술과"}:
            continue
        aliases.append((f"{subject}과", subject))
    return sorted(aliases, key=lambda item: len(item[0]), reverse=True)


class RuleEngine:
    def __init__(self, document: Document) -> None:
        self.doc = document
        self.policy = grade_policy_for(document)
        self.findings: list[ReviewFinding] = []

    def run(self) -> list[ReviewFinding]:
        self._check_standard_sector_presence()
        self._check_grade_curriculum_policy()
        self._check_terms()
        self._check_ratios()
        self._check_consulting_ratio_rules()
        self._check_consulting_overview_format()
        self._check_regular_exam_duplicate_codes()
        self._check_performance_timing_codes()
        self._check_performance_detail_timing_codes()
        self._check_subject_achievement_level()
        self._check_performance_detail_scores()
        self._check_performance_area_name_consistency()
        self._check_performance_linkage_presence()
        self._check_basic_scores()
        self._check_rubric_intervals()
        self._check_excel_relation_engine()
        return self.findings

    def add(self, severity: str, topic: str, anchor: str, lines: list[str], context: str = "") -> None:
        memo = "◆ " + topic
        for line in lines:
            memo += "\n- " + line
        self.findings.append(
            ReviewFinding(
                file_path=self.doc.path,
                grade=self.doc.grade,
                subject=self.doc.subject,
                severity=severity,
                topic=topic,
                anchor_text=anchor,
                memo_text=memo,
                context=context or self.doc.around(anchor),
            )
        )

    def has_related_finding(self, anchor: str, keywords: tuple[str, ...]) -> bool:
        normalized_anchor = normalize_area_name(anchor)
        for finding in self.findings:
            same_anchor = finding.anchor_text == anchor
            haystack = f"{finding.topic}\n{finding.anchor_text}\n{finding.memo_text}\n{finding.context}"
            if not same_anchor and normalized_anchor:
                same_anchor = normalized_anchor in normalize_area_name(haystack)
            if same_anchor and any(keyword in finding.topic for keyword in keywords):
                return True
        return False

    def _check_standard_sector_presence(self) -> None:
        # 섹터 누락은 문서 양식 차이일 수 있으므로 오류가 아닌 확인 필요로만 남긴다.
        if len(self.doc.sectors) < 5:
            return
        required = [
            ("assessment_overview", "4. 평가의 종류와 반영비율"),
            ("performance_detail", "6. 수행평가 세부기준"),
            ("absence", "8. 수행평가 미응시자 및 학적변동자 성적처리"),
        ]
        for sector_id, label in required:
            if sector_id in self.doc.sectors:
                continue
            self.add(
                "하",
                "표준 섹터 확인 필요",
                label,
                [
                    f"{label} 구역을 표준 섹터로 분리하지 못함",
                    "문서 제목 표현 차이 또는 표 추출 문제일 수 있으므로 원문 확인",
                ],
                context=self.doc.text[:1200],
            )

    def _check_terms(self) -> None:
        text = reviewable_text(self.doc)
        if "지필평가" in text:
            self.add("중", "용어 수정", "지필평가", ["지필평가 → 정기시험으로 수정"])
        if "서술형" in text:
            self.add("중", "용어 수정", "서술형", ["서술형 → 논술형으로 수정"])
        if "ㅐ성취기준" in text:
            self.add("중", "표기 수정", "ㅐ성취기준", ["ㅐ성취기준 → 성취기준으로 수정"])

    def _check_ratios(self) -> None:
        ratio_section = sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.")
        ratio_source = ratio_section or self.doc.text
        if self.policy.is_free_semester and has_regular_exam_ratio(ratio_source):
            self.add(
                "상",
                "1학년 자유학기 정기시험 확인",
                "정기시험",
                ["자유학기 평가계획에 정기시험 반영 항목이 있는지 확인", "자유학기 운영 기준에 맞게 정기시험/반영비율 표기 조정"],
                context=ratio_source[:1400],
            )

        mixed_exam_and_performance = "정기시험" in ratio_source and "수행평가" in ratio_source
        seen_ratio_anchors: set[str] = set()
        overview_items = assessment_overview_items(self.doc)
        for area_name, percent in performance_area_percentages_from_ratio(self.doc):
            if percent > 30:
                anchor = f"{area_name} {percent:g}%"
                if anchor in seen_ratio_anchors:
                    continue
                seen_ratio_anchors.add(anchor)
                self.add(
                    "상",
                    "수행평가 한 영역 30% 초과",
                    f"{percent:g}%",
                    [
                        f"{area_name} 영역 반영비율이 {percent:g}%로 30% 초과",
                        "세부영역 분리 또는 반영비율 조정",
                    ],
                    context=ratio_source[:1800],
                )
        detail_section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        for area_name, score, context in extract_performance_text_detail_items(detail_section):
            if score <= 30:
                continue
            anchor = f"{area_name} {score:g}점"
            if anchor in seen_ratio_anchors:
                continue
            seen_ratio_anchors.add(anchor)
            self.add(
                "상",
                "수행평가 한 영역 30% 초과",
                f"{score:g}점",
                [
                    f"6번 수행평가 세부기준에서 '{area_name}' 영역만점이 {score:g}점으로 30점 초과",
                    "한 수행평가 영역이 30%를 넘지 않도록 영역 분리 또는 반영비율 조정",
                ],
                context=context,
            )
        if not overview_items:
            for point, percent in POINT_PERCENT_RE.findall(ratio_source):
                p = float(percent)
                # In mixed tables, large 80~94 point rows are regular-exam selected-response
                # cells, not performance areas. The 30% cap is for each performance area.
                if p > 30 and not (mixed_exam_and_performance and float(point) > 50):
                    self.add(
                        "상",
                        "수행평가 한 영역 30% 초과",
                        f"{point}점 ({percent}%)",
                        ["한 수행평가 영역 반영비율이 30% 초과", "세부영역 분리 또는 반영비율 조정"],
                    )

        if ratio_section:
            percentages = [float(x) for x in PERCENT_RE.findall(ratio_section)]
            if "합계" in ratio_section and percentages:
                # Look for common final total error without overfitting every sub-percent.
                if "100%" not in ratio_section:
                    self.add("상", "반영비율 합계 확인", "합계", ["정기시험+수행평가 합계 100% 여부 확인"])

    def _check_consulting_ratio_rules(self) -> None:
        section = sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.")
        rows = assessment_ratio_table_rows(self.doc)
        source_text = table_rows_text(rows) if rows else section
        if not source_text:
            return

        regular_items = regular_exam_items_from_overview(self.doc)
        if len(regular_items) == 1:
            item = regular_items[0]
            if item.ratio is not None and item.ratio > 50:
                self.add(
                    "상",
                    "정기시험 1회 50% 초과",
                    f"{item.ratio:g}%",
                    [
                        f"정기시험을 1회 실시하면서 반영비율이 {item.ratio:g}%로 50% 초과",
                        "교육지원청 컨설팅 기준에 따라 정기시험 1회 실시 시 50% 이하로 조정",
                    ],
                    context=source_text[:1800],
                )

        essay_percent = essay_ratio_from_overview(self.doc)
        if essay_percent is not None:
            required = 20 if is_pe_arts_subject(self.doc.subject) else 30
            if essay_percent < required:
                self.add(
                    "중",
                    "논술형 평가 비율 확인",
                    f"{essay_percent:g}%",
                    [
                        f"논술형 평가 반영비율이 {essay_percent:g}%로 컨설팅 기준({required}% 이상)보다 낮음",
                        "체육·예술 교과는 20% 이상, 그 외 교과는 30% 이상 여부 확인",
                    ],
                    context=source_text[:1800],
                )

    def _check_consulting_overview_format(self) -> None:
        section = sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.")
        rows = assessment_ratio_table_rows(self.doc)
        source_text = table_rows_text(rows) if rows else section
        if not source_text:
            return

        for context in achievement_code_range_contexts(source_text):
            self.add(
                "중",
                "성취기준 코드 범위 표기",
                "~",
                [
                    "성취기준 코드를 물결표(~)로 범위 표기한 부분이 있음",
                    "컨설팅 기준에 따라 해당 성취기준 코드를 모두 개별 제시",
                ],
                context=context,
            )

        for item in assessment_overview_items(self.doc):
            if item.period and not is_consulting_period_format(item.period):
                self.add(
                    "하",
                    "평가시기 표기 형식 확인",
                    item.period,
                    [
                        f"평가시기 '{item.period}'가 'O월 O주' 형식으로 명확히 표기되었는지 확인 필요",
                        "컨설팅 기준에 따라 평가시기는 O월 O주까지 입력",
                    ],
                    context=item.source,
                )
            if is_vague_performance_area_name(item.name):
                self.add(
                    "중",
                    "평가 영역명 구체화",
                    item.name,
                    [
                        f"평가 영역명 '{item.name}'은 학습내용과 수행활동이 함께 드러나는지 확인 필요",
                        "예: '쓰기'처럼 포괄적인 명칭보다 '주장하는 글쓰기'처럼 교과 내용+수행활동으로 작성",
                    ],
                    context=item.source,
                )

    def _check_basic_scores(self) -> None:
        section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        if not section:
            return
        ratio_scores = performance_area_scores_from_ratio(assessment_ratio_table_rows(self.doc), self.doc)
        if self.doc.table_rows:
            score_rows = extract_basic_score_rows_from_tables(self.doc.table_rows, ratio_scores)
        else:
            score_rows = extract_basic_score_rows(section)
        for full_score, basic_score, context, anchor in score_rows:
            if full_score <= 0:
                continue
            percent = basic_score / full_score * 100
            if percent < 20 or percent > 40:
                self.add(
                    "상",
                    "기본점수 범위 오류",
                    anchor,
                    [
                        f"기본점수 {basic_score}점은 만점 {full_score}점 대비 {percent:.1f}%",
                        "수행평가 기본점수는 만점의 20%~40% 범위로 조정",
                    ],
                    context=context,
                )

    def _check_performance_detail_scores(self) -> None:
        ratio_scores = performance_area_scores_from_ratio(assessment_ratio_table_rows(self.doc), self.doc)
        if not ratio_scores:
            return
        if self.doc.table_rows:
            detail_blocks = performance_detail_blocks_from_tables(self.doc.table_rows)
            for block in detail_blocks:
                expected = match_ratio_score(block["name"], ratio_scores)
                if expected is None:
                    continue
                detail_score = block["score"]
                if detail_score != expected:
                    self.add(
                        "상",
                        "수행평가 영역 만점 불일치",
                        f"{detail_score}점",
                        [
                            f"4번 평가 종류 표의 영역 만점은 {expected}점이나, 6번 수행평가 세부기준은 {detail_score}점으로 표기",
                            "4번 영역 만점과 6번 세부기준 만점을 일치시켜 수정",
                        ],
                        context=table_rows_text(block["rows"][:10]),
                    )
            return

        section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        for name, detail_score, context in extract_performance_text_detail_items(section):
            expected = match_ratio_score(name, ratio_scores)
            if expected is not None and detail_score != expected:
                self.add(
                    "상",
                    "수행평가 영역 만점 불일치",
                    f"{detail_score}점",
                    [
                        f"4번 평가 종류 표의 영역 만점은 {expected}점이나, 6번 수행평가 세부기준은 {detail_score}점으로 표기",
                        "4번 영역 만점과 6번 세부기준 만점을 일치시켜 수정",
                    ],
                    context=context,
                )

    def _check_performance_area_name_consistency(self) -> None:
        ratio_names = performance_area_names_from_ratio(self.doc)
        detail_names = performance_detail_area_names(self.doc)
        if not ratio_names or not detail_names:
            return
        normalized_details = {normalize_area_name(name) for name in detail_names}
        for ratio_name in ratio_names:
            normalized = normalize_area_name(ratio_name)
            if not normalized or normalized in normalized_details:
                continue
            if any(normalized in detail or detail in normalized for detail in normalized_details):
                continue
            self.add(
                "상",
                "4번-6번 수행평가 영역명 불일치",
                ratio_name,
                [
                    f"4번 평가 영역명 '{ratio_name}'이 6번 수행평가 세부기준의 평가영역명과 일치하지 않음",
                    "4번 반영비율 표와 6번 수행평가 세부기준의 평가영역명을 같은 명칭으로 수정",
                ],
                context=(sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.") + "\n" + sector_text(self.doc, "performance_detail", "6. 수행평가", "7."))[:2000],
            )

    def _check_performance_linkage_presence(self) -> None:
        overview_items = [item for item in assessment_overview_items(self.doc) if item.period and "수시" not in item.period]
        if not overview_items:
            return
        plan_text = sector_text(self.doc, "monthly_plan") or monthly_plan_text(self.doc.text)
        if not plan_text:
            return
        for item in overview_items:
            if period_has_assessment_linkage(plan_text, item.period, self.doc.table_rows, item.name):
                continue
            self.add(
                "하",
                "교수학습 운영계획 수업·평가 연계 누락",
                item.period,
                [
                    f"4번 평가표의 '{item.name}' 평가시기({item.period})에 해당하는 월별 계획표 구간에서 평가 연계 표현을 명확히 확인하지 못함",
                    "해당 월/주 수업·평가 방법 또는 수업·평가 연계란에 수행평가 활동을 명시",
                ],
                context=plan_text[:1800],
            )

    def _check_regular_exam_duplicate_codes(self) -> None:
        if self.policy.skip_regular_exam_checks:
            return
        section = sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.")
        rows = assessment_ratio_table_rows(self.doc)
        if not section and not rows:
            return
        source_text = table_rows_text(rows) if rows else section
        if "정기시험" not in source_text or "1차" not in source_text or "2차" not in source_text:
            return
        code_groups = regular_exam_code_groups_from_overview(self.doc)
        if len(code_groups) < 2:
            return
        first, second = code_groups[0], code_groups[1]
        if first and first == second:
            self.add(
                "상",
                "1·2차 정기시험 성취기준 동일",
                "성취기준",
                [
                    "1차와 2차 정기시험 성취기준이 동일함",
                    "1차 범위와 2차 범위 성취기준을 분리하여 작성",
                ],
                context=source_text[:1800],
            )

    def _check_performance_timing_codes(self) -> None:
        section = sector_text(self.doc, "assessment_overview", "4. 평가의 종류", "5.")
        rows = assessment_ratio_table_rows(self.doc)
        if not section and not rows:
            return
        source_text = table_rows_text(rows) if rows else section
        time_cells = table_row_text_cells(rows, "평가 시기") if rows else row_text_cells(section, "평가 시기")
        code_groups = table_row_code_groups(rows, "성취기준") if rows else row_code_groups(section, "성취기준")
        if not time_cells or not code_groups:
            return

        mixed = "정기시험" in source_text and "수행평가" in source_text
        skip = 0
        if mixed:
            if "1차" in source_text and "2차" in source_text and len(time_cells) >= 4 and len(code_groups) >= 4:
                skip = 2
            elif len(time_cells) >= 3 and len(code_groups) >= 3:
                skip = 1
        for time_text, codes in zip(time_cells[skip:], code_groups[skip:]):
            if not codes or "수시" in time_text:
                continue
            plan_text = sector_text(self.doc, "monthly_plan") or self.doc.text
            planned_codes = learned_codes_before_or_by_period(plan_text, time_text, self.doc.table_rows)
            if not planned_codes:
                continue
            missing = sorted(codes - planned_codes)
            if missing:
                self.add(
                    "상",
                    "평가시기-수행 성취기준 불일치",
                    time_text,
                    [
                        f"평가시기 {time_text} 월별 운영계획에 없는 성취기준: {', '.join(missing)}",
                        "평가시기 또는 수행평가 성취기준 수정",
                    ],
                    context=source_text[:1800],
                )

    def _check_performance_detail_timing_codes(self) -> None:
        detail_section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        if not detail_section:
            return
        plan_text = sector_text(self.doc, "monthly_plan") or monthly_plan_text(self.doc.text)
        if not plan_text:
            return

        detail_items = extract_performance_text_detail_items(detail_section)
        if not detail_items and self.doc.table_rows:
            detail_items = [
                (
                    str(block.get("name", "")),
                    int(block.get("score", 0) or 0),
                    table_rows_text(block.get("rows", [])),  # type: ignore[arg-type]
                )
                for block in performance_detail_blocks_from_tables(self.doc.table_rows)
            ]
        if not detail_items:
            return

        overview_by_name = {
            normalize_area_name(item.name): item
            for item in assessment_overview_items(self.doc)
            if normalize_area_name(item.name)
        }
        all_monthly_codes = set(CODE_RE.findall(plan_text))
        reported_missing_from_plan: set[tuple[str, tuple[str, ...]]] = set()
        reported_uncertain_period: set[str] = set()
        for name, _score, context in detail_items:
            codes = set(CODE_RE.findall(context))
            if not codes:
                continue

            matched_item = match_overview_item_by_name(name, overview_by_name)
            period = matched_item.period if matched_item else ""
            if period:
                planned_codes = learned_codes_before_or_by_period(plan_text, period, self.doc.table_rows)
                if planned_codes:
                    missing = sorted(codes - planned_codes)
                    if missing:
                        self.add(
                            "상",
                            "평가시기-수행평가 세부기준 성취기준 불일치",
                            name,
                            [
                                f"6번 수행평가 '{name}'의 성취기준 {', '.join(missing)}이 평가시기({period}) 이전 또는 해당 주차의 월별 교수학습 운영계획에서 확인되지 않음",
                                "1번 월별 교수학습 운영계획의 성취기준 또는 4번 평가시기/6번 수행평가 성취기준 수정",
                            ],
                            context=context[:1800],
                        )
                    continue
                if name not in reported_uncertain_period:
                    reported_uncertain_period.add(name)
                    self.add(
                        "중",
                        "평가시기 월별 성취기준 확인 필요",
                        name,
                        [
                            f"6번 수행평가 '{name}'의 평가시기({period})는 확인되었으나, 해당 월/주차의 월별 교수학습 운영계획 성취기준을 안정적으로 추출하지 못함",
                            "1번 월별 계획표의 월·주·성취기준 표기와 4번 평가시기를 직접 확인",
                        ],
                        context=context[:1800],
                    )
            elif name not in reported_uncertain_period:
                reported_uncertain_period.add(name)
                self.add(
                    "중",
                    "수행평가 평가시기 확인 필요",
                    name,
                    [
                        f"6번 수행평가 '{name}'과 일치하는 4번 평가표의 평가시기를 안정적으로 찾지 못함",
                        "4번 평가의 종류와 반영비율 표의 영역명과 평가시기, 6번 수행평가 영역명을 일치시켜 확인",
                    ],
                    context=context[:1800],
                )

            if all_monthly_codes:
                missing_from_plan = sorted(codes - all_monthly_codes)
                key = (name, tuple(missing_from_plan))
                if missing_from_plan and key not in reported_missing_from_plan:
                    reported_missing_from_plan.add(key)
                    self.add(
                        "상",
                        "월별 계획표에 없는 수행평가 성취기준",
                        name,
                        [
                            f"6번 수행평가 '{name}'의 성취기준 {', '.join(missing_from_plan)}이 1번 월별 교수학습 운영계획 전체에서 확인되지 않음",
                            "월별 진도표에 해당 성취기준을 반영하거나 수행평가 성취기준을 실제 수업 성취기준과 일치하도록 수정",
                        ],
                        context=context[:1800],
                    )

    def _check_subject_achievement_level(self) -> None:
        if not self.doc.subject:
            return
        text = sector_text(self.doc, "achievement_level", "3. 성취기준", "4.") or self.doc.text
        arts = {"체육", "음악", "미술"}
        if self.doc.subject in arts:
            if re.search(r"\bD\b|\bE\b", text):
                self.add("중", "성취도 체계 확인", "성취도", ["체육·예술 교과는 A~C 체계 적용 여부 확인"])
        elif self.doc.grade == 1:
            # 1학년 2022 개정 양식에서는 수행평가 성취수준 A~E 표기가 있을 수 있으므로
            # A~E 자체를 오류로 보지 않는다. 형식 포함 여부는 학년 정책 점검에서만 확인한다.
            return
        elif self.doc.grade in {1, 2, 3}:
            if "A" in text and "E" not in text and "성취도" in text:
                self.add("하", "성취도 체계 확인", "성취도", ["일반교과 A~E 성취도 체계 누락 여부 확인"])

    def _check_grade_curriculum_policy(self) -> None:
        text = self.doc.text
        if self.doc.grade is None:
            self.add(
                "하",
                "학년 확인 필요",
                self.doc.path.name,
                ["파일명 또는 본문에서 학년을 명확히 추정하지 못함", "학년별 교육과정 기준 적용을 위해 학년 표기 확인"],
                context=text[:900],
            )
            return

        if self.doc.grade in {1, 2}:
            return
        elif self.doc.grade == 3:
            if not contains_2015_curriculum(text):
                self.add(
                    "하",
                    "3학년 2015 개정 교육과정 확인",
                    "평가기준",
                    ["3학년은 2015 개정 교육과정 기준 적용", "'평가기준' 용어 사용 자체는 오류가 아니나 2015 개정 기준 여부 확인"],
                    context=curriculum_context(text),
                )

    def _check_performance_criteria_mismatch(self) -> None:
        text = self.doc.text
        section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        if not section:
            return
        blocks = split_performance_blocks(section)
        for block in blocks:
            edu_part = section_between(block, "교육과정 성취기준", "평가 기준") or section_between(block, "교육과정 성취기준", "평가기준")
            criteria_part = section_between(block, "평가 기준", "평가 요소") or section_between(block, "평가기준", "평가요소")
            if not edu_part or not criteria_part:
                continue
            edu_codes = set(CODE_RE.findall(edu_part))
            criteria_codes = set(CODE_RE.findall(criteria_part))
            extra = sorted(criteria_codes - edu_codes)
            if extra:
                anchor = extra[0]
                self.add(
                    "상",
                    "수행평가 성취기준-평가기준 불일치",
                    anchor,
                    [
                        "교육과정 성취기준과 평가기준 코드 불일치",
                        f"평가기준에만 있는 코드: {', '.join(extra)}",
                        "해당 수행평가 성취기준과 코드 일치 필요",
                    ],
                    context=block[:1200],
                )

    def _check_rubric_intervals(self) -> None:
        if self.doc.table_rows:
            for anchor, context in extract_score_range_only_rows_from_tables(self.doc.table_rows):
                self.add(
                    "중",
                    "채점기준 범위형 배점",
                    anchor,
                    [
                        f"{anchor}처럼 점수 범위만 제시되어 구체적 채점 기준 확인이 어려움",
                        "각 배점별 채점 기준을 구체적으로 분리하여 작성",
                    ],
                    context=context,
                )
            for item in extract_table_rubric_ladders(self.doc.table_rows):
                scores = item["scores"]
                if len(scores) < 4:
                    continue
                element_score = item.get("element_score")
                max_score = max(scores)
                if isinstance(element_score, int) and element_score > 0 and max_score != element_score:
                    element_name = str(item.get("element_name") or item.get("top_anchor") or "평가요소")
                    self.add(
                        "상",
                        "평가요소 만점-채점표 배점 불일치",
                        element_name,
                        [
                            f"{element_name} 평가요소는 {element_score}점으로 표기되었으나 채점표 최고 배점은 {max_score}점",
                            "평가요소 만점과 해당 채점 기준 최고 배점을 일치하도록 수정",
                        ],
                        context=item["context"],
                    )
                interval_scores = comparable_interval_scores(scores)
                diffs = [interval_scores[i] - interval_scores[i + 1] for i in range(len(interval_scores) - 1)]
                if len(interval_scores) >= 4 and all(diff > 0 for diff in diffs) and len(set(diffs)) > 1:
                    score_text = "-".join(str(s) for s in interval_scores)
                    anchor = str(item.get("score_anchor") or item.get("top_anchor") or scores[min(2, len(scores) - 1)])
                    self.add(
                        "중",
                        "배점 급간 불균등",
                        anchor,
                        [
                            f"{score_text}로 감소 폭 불규칙",
                            "배점 급간 불균등은 수정 필수: 균등 급간으로 조정",
                        ],
                        context=item["context"],
                    )

                min_score = min(scores)
                if max_score > 0:
                    percent = min_score / max_score * 100
                    if percent < 20 or percent > 40:
                        self.add(
                            "상",
                            "평가요소 최하점 범위 오류",
                            str(item.get("min_anchor") or min_score),
                            [
                                f"평가요소 최하점 {min_score}점은 요소 만점 {max_score}점 대비 {percent:.1f}%",
                                "평가요소별 최하점도 요소 만점의 20%~40% 범위로 조정",
                            ],
                            context=item["context"],
                        )

            return

        text = self.doc.text
        section = sector_text(self.doc, "performance_detail", "6. 수행평가", "7.")
        if not section:
            return
        blocks = split_performance_blocks(section)
        for anchor, context in extract_score_range_only_rows_from_text(section):
            self.add(
                "중",
                "채점기준 범위형 배점",
                anchor,
                [
                    f"{anchor}처럼 점수 범위만 제시되어 구체적 채점 기준 확인이 어려움",
                    "각 배점별 채점 기준을 구체적으로 분리하여 작성",
                ],
                context=context,
            )
        for element_name, element_score, max_score, context in extract_text_element_score_mismatches(section):
            self.add(
                "상",
                "평가요소 만점-채점표 배점 불일치",
                element_name,
                [
                    f"{element_name} 평가요소는 {element_score}점으로 표기되었으나 채점표 최고 배점은 {max_score}점",
                    "평가요소 만점과 해당 채점 기준 최고 배점을 일치하도록 수정",
                ],
                context=context,
            )
        for block in blocks:
            rows = extract_rubric_rows(block)
            for anchor, scores, is_explicit_rubric in rows:
                if not is_explicit_rubric or len(scores) < 4:
                    continue
                diffs = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
                if len(set(diffs)) > 1:
                    score_text = "-".join(str(s) for s in scores)
                    self.add(
                        "중",
                        "배점 급간 불균등",
                        anchor,
                        [
                            f"{score_text}로 감소 폭 불규칙",
                            "배점 급간 불균등은 수정 필수: 균등 급간으로 조정",
                        ],
                        context=block[:1600],
                    )

    def _check_excel_relation_engine(self) -> None:
        for row in build_excel_relation_audit(self.doc):
            if row.status == "Pass":
                continue
            if row.check_item == "4번-6번 영역만점":
                if self.has_related_finding(row.key, ("영역 만점", "만점 불일치")):
                    continue
                topic = "엑셀형 관계검증: 4번-6번 영역만점 불일치"
                suggestion = "4번 평가의 종류와 반영비율 표의 영역만점과 6번 수행평가 세부기준의 영역만점을 일치시켜 수정"
            elif row.check_item == "4번-6번 성취기준":
                if self.has_related_finding(row.key, ("성취기준",)):
                    continue
                topic = "엑셀형 관계검증: 4번-6번 성취기준 불일치"
                suggestion = "4번 평가표와 6번 수행평가 세부기준의 성취기준을 같은 평가영역 기준으로 일치시켜 수정"
            elif row.check_item == "6번-1번 성취기준 전체":
                if self.has_related_finding(row.key, ("성취기준", "월별 계획표")):
                    continue
                topic = "엑셀형 관계검증: 월별 계획표에 없는 수행평가 성취기준"
                suggestion = "1번 월별 교수학습 운영계획에 해당 성취기준을 반영하거나 6번 수행평가 성취기준을 수정"
            elif row.check_item == "평가시기 이전 학습":
                if self.has_related_finding(row.key, ("평가시기", "성취기준")):
                    continue
                topic = "엑셀형 관계검증: 평가시기 이전 성취기준 확인"
                suggestion = "평가시기 이전 또는 해당 주차까지 월별 진도표에 성취기준이 등장하도록 평가시기나 성취기준을 조정"
            elif row.check_item == "4번-6번 영역 매칭":
                if self.has_related_finding(row.key, ("영역명", "평가시기")):
                    continue
                topic = "엑셀형 관계검증: 수행평가 영역 매칭 확인 필요"
                suggestion = "4번 평가표와 6번 수행평가 세부기준의 영역명을 동일하게 작성"
            else:
                topic = f"엑셀형 관계검증: {row.check_item}"
                suggestion = "엑셀형 관계검증 결과를 기준으로 원문 표 확인"

            self.add(
                row.severity,
                topic,
                row.key,
                [
                    row.opinion,
                    f"기준값: {row.left_value or '-'} / 비교값: {row.right_value or '-'}",
                    suggestion,
                ],
                context=row.context,
            )


def build_excel_relation_audit(document: Document) -> list[RelationAuditRow]:
    """Create spreadsheet-style relationship checks between the three core tables."""
    rows: list[RelationAuditRow] = []
    plan_text = sector_text(document, "monthly_plan") or monthly_plan_text(document.text)
    monthly_codes = set(CODE_RE.findall(plan_text))
    overview_items = assessment_overview_items(document)
    detail_items = performance_detail_relation_items(document)
    overview_by_name = {normalize_area_name(item.name): item for item in overview_items if normalize_area_name(item.name)}
    detail_by_name = {normalize_area_name(item["name"]): item for item in detail_items if normalize_area_name(str(item["name"]))}

    for overview in overview_items:
        matched_detail = match_relation_detail_by_name(overview.name, detail_by_name)
        if not matched_detail:
            rows.append(
                RelationAuditRow(
                    "assessment_overview ↔ performance_detail",
                    "4번-6번 영역 매칭",
                    overview.name,
                    overview.name,
                    "",
                    "확인 필요",
                    f"4번 평가표의 수행평가 영역 '{overview.name}'에 대응하는 6번 수행평가 세부기준 영역을 찾지 못함",
                    "중",
                    overview.source,
                )
            )

    for detail in detail_items:
        name = str(detail["name"])
        detail_score = detail.get("score")
        detail_codes = set(detail.get("codes", set()))
        context = str(detail.get("context", ""))
        overview = match_overview_item_by_name(name, overview_by_name)
        if not overview:
            rows.append(
                RelationAuditRow(
                    "assessment_overview ↔ performance_detail",
                    "4번-6번 영역 매칭",
                    name,
                    "",
                    name,
                    "확인 필요",
                    f"6번 수행평가 '{name}'과 일치하는 4번 평가표 영역을 안정적으로 찾지 못함",
                    "중",
                    context,
                )
            )
        else:
            if isinstance(detail_score, int) and isinstance(overview.score, int):
                status = "Pass" if detail_score == overview.score else "Fail"
                rows.append(
                    RelationAuditRow(
                        "assessment_overview ↔ performance_detail",
                        "4번-6번 영역만점",
                        name,
                        f"{overview.score}점",
                        f"{detail_score}점",
                        status,
                        (
                            "4번 평가표와 6번 수행평가 세부기준의 영역만점이 일치함"
                            if status == "Pass"
                            else f"4번 평가표 영역만점 {overview.score}점과 6번 세부기준 영역만점 {detail_score}점이 다름"
                        ),
                        "상" if status == "Fail" else "하",
                        context,
                    )
                )

            overview_codes = set(overview.achievement_codes)
            if detail_codes and overview_codes:
                missing = sorted(detail_codes - overview_codes)
                status = "Pass" if not missing else "Fail"
                rows.append(
                    RelationAuditRow(
                        "assessment_overview ↔ performance_detail",
                        "4번-6번 성취기준",
                        name,
                        ", ".join(sorted(overview_codes)),
                        ", ".join(sorted(detail_codes)),
                        status,
                        (
                            "6번 수행평가 성취기준이 4번 평가표 성취기준 안에서 확인됨"
                            if status == "Pass"
                            else f"6번 성취기준 중 4번 평가표에서 확인되지 않는 코드: {', '.join(missing)}"
                        ),
                        "상" if status == "Fail" else "하",
                        context,
                    )
                )

            if detail_codes and overview.period:
                learned_codes = learned_codes_before_or_by_period(plan_text, overview.period, document.table_rows)
                if learned_codes:
                    missing_by_time = sorted(detail_codes - learned_codes)
                    status = "Pass" if not missing_by_time else "Fail"
                    rows.append(
                        RelationAuditRow(
                            "monthly_plan ↔ performance_detail",
                            "평가시기 이전 학습",
                            name,
                            f"{overview.period}: {', '.join(sorted(learned_codes))}",
                            ", ".join(sorted(detail_codes)),
                            status,
                            (
                                "수행평가 성취기준이 평가시기 이전 또는 해당 주차까지 월별 계획에서 확인됨"
                                if status == "Pass"
                                else f"평가시기({overview.period}) 이전/해당 주차까지 확인되지 않는 성취기준: {', '.join(missing_by_time)}"
                            ),
                            "상" if status == "Fail" else "하",
                            context,
                        )
                    )
                else:
                    rows.append(
                        RelationAuditRow(
                            "monthly_plan ↔ performance_detail",
                            "평가시기 이전 학습",
                            name,
                            overview.period,
                            ", ".join(sorted(detail_codes)),
                            "확인 필요",
                            "평가시기는 확인되었으나 해당 월/주차의 월별 성취기준을 안정적으로 추출하지 못함",
                            "중",
                            context,
                        )
                    )

        if detail_codes and monthly_codes:
            missing_monthly = sorted(detail_codes - monthly_codes)
            status = "Pass" if not missing_monthly else "Fail"
            rows.append(
                RelationAuditRow(
                    "monthly_plan ↔ performance_detail",
                    "6번-1번 성취기준 전체",
                    name,
                    ", ".join(sorted(monthly_codes)),
                    ", ".join(sorted(detail_codes)),
                    status,
                    (
                        "6번 수행평가 성취기준이 1번 월별 계획 전체에서 확인됨"
                        if status == "Pass"
                        else f"1번 월별 계획 전체에서 확인되지 않는 수행평가 성취기준: {', '.join(missing_monthly)}"
                    ),
                    "상" if status == "Fail" else "하",
                    context,
                )
            )

    return rows


def performance_detail_relation_items(document: Document) -> list[dict[str, object]]:
    if document.table_rows:
        table_items: list[dict[str, object]] = []
        for block in performance_detail_blocks_from_tables(document.table_rows):
            context = table_rows_text(block.get("rows", []))  # type: ignore[arg-type]
            table_items.append(
                {
                    "name": str(block.get("name", "")),
                    "score": int(block.get("score", 0) or 0),
                    "codes": set(CODE_RE.findall(context)),
                    "context": context,
                }
            )
        if table_items:
            return table_items

    section = sector_text(document, "performance_detail", "6. 수행평가", "7.")
    return [
        {"name": name, "score": score, "codes": set(CODE_RE.findall(context)), "context": context}
        for name, score, context in extract_performance_text_detail_items(section)
    ]


def match_relation_detail_by_name(name: str, detail_by_name: dict[str, dict[str, object]]) -> dict[str, object] | None:
    normalized = normalize_area_name(name)
    if not normalized:
        return None
    if normalized in detail_by_name:
        return detail_by_name[normalized]
    for detail_name, item in detail_by_name.items():
        if normalized in detail_name or detail_name in normalized:
            return item
    return None


class HwpxMemoWriter:
    @classmethod
    def copy_with_memos(cls, src: Path, dst: Path, findings: list[ReviewFinding]) -> dict[str, object]:
        with zipfile.ZipFile(src, "r") as zin:
            entries = [(info, zin.read(info.filename)) for info in zin.infolist()]

        inserted = []
        inserted_indexes: set[int] = set()
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for info, data in entries:
                if info.filename == "Contents/header.xml":
                    data = cls.ensure_memo_properties(data.decode("utf-8")).encode("utf-8")
                elif info.filename.startswith("Contents/section") and info.filename.endswith(".xml"):
                    section = cls.strip_existing_memos(data.decode("utf-8"))
                    for offset, finding in enumerate(findings, start=1):
                        if offset in inserted_indexes:
                            continue
                        section, ok = cls.insert_memo_near_anchor(section, finding, offset)
                        if ok:
                            inserted.append(finding.anchor_text)
                            inserted_indexes.add(offset)
                    data = section.encode("utf-8")
                zout.writestr(info, data)
        missing = [finding.anchor_text for offset, finding in enumerate(findings, start=1) if offset not in inserted_indexes]
        return {"source": str(src), "output": str(dst), "inserted": inserted, "missing": missing}

    @staticmethod
    def ensure_memo_properties(header: str) -> str:
        if "<hh:memoProperties" in header:
            return header
        memo_pr = (
            '<hh:memoProperties itemCnt="1">'
            '<hh:memoPr id="1" width="15591" lineWidth="1" lineType="SOLID" '
            'lineColor="#6AA84F" fillColor="#D9EAD3" activeColor="#B6D7A8" memoType="NOMAL"/>'
            "</hh:memoProperties>"
        )
        return header.replace("</hh:refList>", memo_pr + "</hh:refList>")

    @staticmethod
    def strip_existing_memos(section: str) -> str:
        memo_ids = re.findall(
            r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")(?=[^>]*\bid="([^"]+)")[\s\S]*?</hp:fieldBegin></hp:ctrl>',
            section,
        )
        section = re.sub(
            r'<hp:ctrl><hp:fieldBegin\b(?=[^>]*\btype="MEMO")[\s\S]*?</hp:fieldBegin></hp:ctrl>',
            "",
            section,
        )
        for memo_id in memo_ids:
            section = re.sub(
                rf'<hp:ctrl><hp:fieldEnd\b(?=[^>]*\bbeginIDRef="{re.escape(memo_id)}")[^>]*/></hp:ctrl><hp:t/>',
                "",
                section,
            )
            section = re.sub(
                rf'<hp:ctrl><hp:fieldEnd\b(?=[^>]*\bbeginIDRef="{re.escape(memo_id)}")[^>]*/></hp:ctrl>',
                "",
                section,
            )
        return section

    @classmethod
    def insert_memo_near_anchor(cls, section: str, finding: ReviewFinding, number: int) -> tuple[str, bool]:
        candidates = anchor_candidates(finding)
        for candidate in candidates:
            section, ok = cls.insert_exact_text(section, candidate, finding.memo_text, number, finding.context)
            if ok:
                return section, True
        return section, False

    @classmethod
    def insert_exact_text(cls, section: str, anchor: str, memo_text: str, number: int, context: str = "") -> tuple[str, bool]:
        escaped = html.escape(anchor, quote=False)
        matches: list[tuple[int, int, str]] = []
        for node in (f"<hp:t>{escaped}</hp:t>", f"<hp:t>{escaped} </hp:t>"):
            start = 0
            while True:
                idx = section.find(node, start)
                if idx < 0:
                    break
                matches.append((idx, idx + len(node), node))
                start = idx + len(node)

        pattern = re.compile(rf"(<hp:t[^>]*>)([^<]*{re.escape(escaped)}[^<]*)(</hp:t>)")
        for match in pattern.finditer(section):
            matches.append((match.start(), match.end(), match.group(0)))

        if matches:
            start, end, node = max(
                matches,
                key=lambda item: context_match_score(section, item[0], item[1], context),
            )
            replacement = cls.memo_begin(number, memo_text) + node + cls.memo_end(number)
            return section[:start] + replacement + section[end:], True
        return section, False

    @staticmethod
    def memo_begin(number: int, memo_text: str) -> str:
        msg = html.escape(memo_text, quote=False).replace("\n", "<hp:lineBreak/>")
        begin_id = 1430000000 + number
        serial = 73000000 + number
        return (
            f'<hp:ctrl><hp:fieldBegin id="{begin_id}" type="MEMO" name="" editable="1" '
            f'dirty="1" zorder="{number}" fieldid="623209829" metaTag="">'
            f'<hp:parameters cnt="7" name="">'
            f'<hp:integerParam name="Prop">0</hp:integerParam>'
            f'<hp:stringParam name="Command">MEMO/65535/{number}/1822614864/{serial}/AI교사/\\;;</hp:stringParam>'
            f'<hp:stringParam name="ID">memo{number}</hp:stringParam>'
            f'<hp:integerParam name="Number">{number}</hp:integerParam>'
            f'<hp:stringParam name="Author">AI교사</hp:stringParam>'
            f'<hp:stringParam name="MemoShapeIDRef">65535</hp:stringParam>'
            f'<hp:stringParam name="CreateDateTime">2026-06-30T09:00:00Z</hp:stringParam>'
            f"</hp:parameters>"
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" '
            f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f'<hp:p id="0" paraPrIDRef="11" styleIDRef="17" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="4"><hp:t>{msg}</hp:t></hp:run>'
            f"</hp:p></hp:subList></hp:fieldBegin></hp:ctrl>"
        )

    @staticmethod
    def memo_end(number: int) -> str:
        return f'<hp:ctrl><hp:fieldEnd beginIDRef="{1430000000 + number}" fieldid="623209829"/></hp:ctrl><hp:t/>'


def normalize_text(text: str) -> str:
    return text.replace("\u00a0", " ").replace("\r\n", "\n")


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def read_hwpx_section(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        section_names = [
            name
            for name in zf.namelist()
            if re.fullmatch(r"Contents/section\d+\.xml", name)
        ]
        if not section_names:
            return ""
        section_names.sort(key=lambda name: int(re.search(r"section(\d+)\.xml", name).group(1)))
        return "\n".join(
            zf.read(name).decode("utf-8", errors="ignore")
            for name in section_names
        )


def extract_visible_text(section_xml: str) -> str:
    text = re.sub(r"<hp:lineBreak\s*/>", "\n", section_xml)
    texts = re.findall(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", text, re.S)
    return normalize_text("\n".join(html.unescape(t) for t in texts))


def context_match_score(section: str, start: int, end: int, context: str) -> int:
    if not context:
        return 0
    window = section[max(0, start - 20000) : min(len(section), end + 20000)]
    visible = extract_visible_text(window)
    score = 0
    for token in context_tokens(context):
        if token in visible:
            score += 1
    return score


def context_tokens(text: str) -> list[str]:
    cleaned = clean_cell(text)
    tokens: list[str] = []
    tokens.extend(CODE_RE.findall(cleaned))
    tokens.extend(re.findall(r"\d+월\s*\d+(?:~\d+)?주", cleaned))
    tokens.extend(re.findall(r"\d+(?:\.\d+)?점\s*\(\s*\d+(?:\.\d+)?%\s*\)", cleaned))
    tokens.extend(re.findall(r"[0-9A-Za-z가-힣·․~()]{4,}", cleaned))
    seen = set()
    result = []
    for token in tokens:
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result[:80]


def xml_int_attr(xml: str, name: str, default: int = 1) -> int:
    match = re.search(rf'\b{name}="(\d+)"', xml)
    return int(match.group(1)) if match else default


def extract_hwpx_table_rows(section_xml: str) -> list[list[str]]:
    rows: list[list[str]] = []
    row_span_cells: dict[int, tuple[int, str]] = {}
    for row_xml in re.findall(r"<hp:tr\b[\s\S]*?</hp:tr>", section_xml):
        cells: list[str] = []
        current_col = 0
        touched_spans: set[int] = set()
        for cell_xml in re.findall(r"<hp:tc\b[\s\S]*?</hp:tc>", row_xml):
            cell_addr_match = re.search(r"<hp:cellAddr\b[^>]*/?>", cell_xml)
            cell_span_match = re.search(r"<hp:cellSpan\b[^>]*/?>", cell_xml)
            target_col = xml_int_attr(cell_addr_match.group(0), "colAddr", current_col) if cell_addr_match else current_col
            while current_col < target_col:
                if current_col in row_span_cells:
                    _, span_text = row_span_cells[current_col]
                    cells.append(span_text)
                    touched_spans.add(current_col)
                else:
                    cells.append("")
                current_col += 1

            cell_text = extract_visible_text(cell_xml)
            cell_text = re.sub(r"\s+", " ", cell_text).strip()
            col_span = xml_int_attr(cell_span_match.group(0), "colSpan", 1) if cell_span_match else 1
            row_span = xml_int_attr(cell_span_match.group(0), "rowSpan", 1) if cell_span_match else 1
            for _ in range(max(1, col_span)):
                cells.append(cell_text)
            if row_span > 1:
                for offset in range(col_span):
                    row_span_cells[target_col + offset] = (row_span - 1, cell_text)
            current_col = target_col + col_span

        for col in sorted(row_span_cells):
            if col >= current_col:
                _, span_text = row_span_cells[col]
                cells.append(span_text)
                touched_spans.add(col)

        for col in list(touched_spans):
            remaining, span_text = row_span_cells[col]
            if remaining <= 1:
                row_span_cells.pop(col, None)
            else:
                row_span_cells[col] = (remaining - 1, span_text)
        if any(cells):
            rows.append(cells)
    return rows


def infer_grade(path: Path, text: str) -> int | None:
    sample = f"{path.name}\n{text[:2000]}"
    match = re.search(r"([123])\s*학년", sample)
    return int(match.group(1)) if match else None


def infer_subject(path: Path, text: str) -> str | None:
    filename = path.name
    normalized_filename = re.sub(r"\s+", "", filename)

    filename_patterns = [
        ("기술가정", ["기술가정과", "기술가정"]),
        ("기술가정", ["기술과"]),
        ("국어", ["국어과"]),
        ("도덕", ["도덕과"]),
        ("사회", ["사회과"]),
        ("역사", ["역사과"]),
        ("수학", ["수학과"]),
        ("과학", ["과학과"]),
        ("체육", ["체육과"]),
        ("음악", ["음악과"]),
        ("미술", ["미술과"]),
        ("영어", ["영어과"]),
        ("한문", ["한문과"]),
        ("정보", ["정보과"]),
    ]
    for subject, patterns in filename_patterns:
        for pattern in patterns:
            if pattern in normalized_filename:
                return subject

    for subject in SUBJECTS:
        if subject in filename and not (subject == "수학" and "교수학습" in filename):
            if subject == "기술과":
                return "기술가정"
            return subject
    sample = text[:2000]
    for subject in SUBJECTS:
        if subject in sample:
            if subject == "기술과":
                return "기술가정"
            return subject
    return None


def contains_2022_curriculum(text: str) -> bool:
    return bool(re.search(r"2022\s*개정|22\s*개정|2022\s*년?\s*교육과정|22\s*년?\s*교육과정", text))


def contains_2015_curriculum(text: str) -> bool:
    return bool(re.search(r"2015\s*개정|15\s*개정|2015\s*년?\s*교육과정|15\s*년?\s*교육과정|평가기준", text))


def missing_2022_achievement_level_sections(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    checks = [
        ("성취기준별 성취수준", ("성취기준별성취수준", "성취기준에따른성취수준")),
        ("학기 단위 성취수준", ("학기단위성취수준", "학기말성취수준")),
    ]
    missing = []
    for label, patterns in checks:
        if not any(pattern in compact for pattern in patterns):
            missing.append(label)
    return missing


def curriculum_context(text: str) -> str:
    keywords = ["2022", "22개정", "2015", "15개정", "성취수준", "평가기준", "교육과정"]
    positions = [text.find(keyword) for keyword in keywords if text.find(keyword) >= 0]
    if not positions:
        return text[:1200]
    start = max(0, min(positions) - 500)
    return text[start : start + 1600]


def has_regular_exam_ratio(text: str) -> bool:
    if "정기시험" not in text:
        return False
    if not ("평가 종류" in text or "평가의 종류" in text or "반영비율" in text or "시기/영역" in text):
        return False
    if "1차" in text or "2차" in text:
        return True
    return bool(re.search(r"정기시험[\s\S]{0,160}\d+(?:\.\d+)?\s*%", text))


SECTOR_PATTERNS: list[tuple[str, str, tuple[str, ...]]] = [
    ("document_info", "문서 기본 정보", (r"2026학년도[\s\S]{0,120}?교수\s*[·ㆍ∙]?\s*학습\s*및\s*평가", r"학교명\s+학년\s+과목\s+학기")),
    ("monthly_plan", "교수·학습 운영 계획", (r"교수\s*[·ㆍ∙]?\s*학습\s*운영\s*계획", r"월\s+주\s+단원명")),
    ("purpose", "평가의 목적", (r"(?:^|\n)\s*1\s*(?:\.|\n|\s)+평가의\s*목적", r"평가의\s*목적")),
    ("direction", "평가의 기본 방향과 방침", (r"(?:^|\n)\s*2\s*(?:\.|\n|\s)+평가의\s*기본\s*방향(?:과|및)?\s*방침", r"평가의\s*기본\s*방향")),
    ("achievement_level", "성취기준 및 성취수준", (r"(?:^|\n)\s*3\s*(?:\.|\n|\s)+성취기준\s*및\s*성취수준", r"(?:^|\n)\s*3\s*(?:\.|\n|\s)+성취기준\s*및\s*평가기준", r"성취기준별\s*성취수준", r"학기\s*단위\s*성취수준")),
    ("assessment_overview", "평가의 종류와 반영비율", (r"(?:^|\n)\s*4\s*(?:\.|\n|\s)+평가의\s*종류\s*와\s*반영\s*비율", r"(?:^|\n)\s*4\s*(?:\.|\n|\s)+평가의\s*종류\s*와\s*반영비율", r"평가\s*종류[\s\S]{0,120}?반영\s*비율")),
    ("achievement_rate", "성취율과 성취도", (r"(?:^|\n)\s*(?:5|6)\s*(?:\.|\n|\s)+성취율\s*과\s*성취도", r"성취율\s*과\s*성취도")),
    ("performance_detail", "수행평가 세부기준", (r"(?:^|\n)\s*(?:5|6)\s*(?:\.|\n|\s)+수행평가\s*세부\s*기준", r"평가영역명[\s\S]{0,80}?\(\d{1,3}점\)")),
    ("affective", "정의적 능력 평가", (r"(?:^|\n)\s*(?:6|7)\s*(?:\.|\n|\s)+정의적\s*능력\s*평가", r"정의적\s*능력")),
    ("absence", "수행평가 미응시자 및 학적변동자 성적처리", (r"(?:^|\n)\s*(?:7|8)\s*(?:\.|\n|\s)+수행평가\s*미응시자", r"미응시자\s*및\s*학적\s*변동자", r"학적\s*변동자\s*처리", r"학적\s*변동자\s*성적처리")),
    ("notice", "평가 유의사항", (r"(?:^|\n)\s*(?:8|9)\s*(?:\.|\n|\s)+평가\s*유의사항", r"평가\s*유의\s*사항")),
    ("analysis", "평가 결과 분석 및 활용", (r"(?:^|\n)\s*(?:9|10)\s*(?:\.|\n|\s)+평가\s*결과\s*분석", r"평가\s*결과\s*분석\s*및\s*활용")),
]


def split_standard_sectors(text: str) -> dict[str, StandardSector]:
    matches: list[tuple[int, str, str, str, str]] = []
    for sector_id, title, patterns in SECTOR_PATTERNS:
        best: tuple[int, str, str, str, str] | None = None
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.MULTILINE)
            if not match:
                continue
            confidence = "높음" if pattern.startswith("(?:^|\\n)") or pattern.startswith("2026") else "중간"
            candidate = (match.start(), sector_id, title, match.group(0), confidence)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is not None:
            matches.append(best)

    matches.sort(key=lambda item: item[0])
    filtered: list[tuple[int, str, str, str, str]] = []
    for item in matches:
        start, sector_id, *_ = item
        if filtered and start - filtered[-1][0] < 40:
            previous = filtered[-1]
            previous_order = sector_order(previous[1])
            current_order = sector_order(sector_id)
            if current_order < previous_order:
                filtered[-1] = item
            continue
        filtered.append(item)

    sectors: dict[str, StandardSector] = {}
    for index, (start, sector_id, title, _anchor, confidence) in enumerate(filtered):
        end = filtered[index + 1][0] if index + 1 < len(filtered) else len(text)
        sector_body = text[start:end].strip()
        if not sector_body:
            continue
        sectors[sector_id] = StandardSector(sector_id, title, start, end, sector_body, confidence)
    return sectors


def sector_order(sector_id: str) -> int:
    for index, (candidate, _title, _patterns) in enumerate(SECTOR_PATTERNS):
        if candidate == sector_id:
            return index
    return len(SECTOR_PATTERNS)


def sector_text(doc: Document, sector_id: str, fallback_start: str = "", fallback_end: str = "") -> str:
    sector = doc.sectors.get(sector_id)
    if sector:
        return sector.text
    if fallback_start:
        return section_after(doc.text, fallback_start, fallback_end)
    return ""


STAGE_DEFINITIONS: tuple[tuple[int, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        1,
        "문서 구조 진단",
        ("document_info", "monthly_plan", "assessment_overview", "performance_detail"),
        ("표준 섹터", "교육과정", "학년"),
    ),
    (
        2,
        "평가의 종류와 반영비율 점검",
        ("assessment_overview",),
        ("반영비율", "정기시험", "논술형", "수행평가 한 영역", "1·2차", "1차", "2차"),
    ),
    (
        3,
        "성취기준-평가시기 대조",
        ("monthly_plan", "assessment_overview", "performance_detail"),
        (
            "평가시기-수행평가 세부기준 성취기준",
            "월별 계획표에 없는 수행평가 성취기준",
            "평가시기 월별 성취기준",
            "수행평가 평가시기 확인",
            "성취기준 불일치",
        ),
    ),
    (
        4,
        "수행평가 세부기준 점검",
        ("performance_detail",),
        ("기본점수", "최하점", "배점 급간", "성취기준-평가기준", "채점", "만점", "세부기준"),
    ),
    (
        5,
        "4번-6번 수행평가 비교",
        ("assessment_overview", "performance_detail"),
        ("4번-6번", "영역명", "영역 만점", "만점 불일치", "수행평가 영역"),
    ),
    (
        6,
        "월별계획-수업평가연계 비교",
        ("monthly_plan", "assessment_overview"),
        ("평가시기", "월별", "수업·평가", "수업-평가", "연계", "학습 이전"),
    ),
    (
        7,
        "미응시자 처리 비교",
        ("performance_detail", "absence"),
        ("미응시", "백지", "미참여", "결석", "학적변동"),
    ),
)


def build_stage_results(doc: Document, findings: list[ReviewFinding]) -> list[StageReviewResult]:
    results: list[StageReviewResult] = []
    classified_ids: set[int] = set()
    for stage_no, stage_name, required_sectors, keywords in STAGE_DEFINITIONS:
        missing = [sector_title(sector_id) for sector_id in required_sectors if sector_id not in doc.sectors]
        stage_findings = [
            finding
            for finding in findings
            if id(finding) not in classified_ids and finding_matches_stage(finding, keywords)
        ]
        for finding in stage_findings:
            classified_ids.add(id(finding))

        if missing and stage_no in {4, 5, 6, 7}:
            status = "건너뜀"
            message = "필수 섹터 인식 부족: " + ", ".join(missing)
            count = 0
        elif missing:
            status = "확인 필요"
            message = "필수 섹터 인식 부족: " + ", ".join(missing)
            count = len(stage_findings)
        elif stage_findings:
            status = "문제 발견"
            count = len(stage_findings)
            topics = unique_preserve_order(finding.topic for finding in stage_findings)
            message = f"{count}건: " + ", ".join(topics[:4])
        else:
            status = "이상없음"
            count = 0
            message = "해당 단계에서 자동 검출된 오류 후보가 없습니다."

        results.append(
            StageReviewResult(
                file_path=doc.path,
                grade=doc.grade,
                subject=doc.subject,
                stage_no=stage_no,
                stage_name=stage_name,
                status=status,
                message=message,
                finding_count=count,
            )
        )

    remaining = [finding for finding in findings if id(finding) not in classified_ids]
    if remaining:
        topics = unique_preserve_order(finding.topic for finding in remaining)
        results.append(
            StageReviewResult(
                file_path=doc.path,
                grade=doc.grade,
                subject=doc.subject,
                stage_no=99,
                stage_name="기타 규칙 점검",
                status="문제 발견",
                message=f"{len(remaining)}건: " + ", ".join(topics[:4]),
                finding_count=len(remaining),
            )
        )
    return results


def finding_matches_stage(finding: ReviewFinding, keywords: tuple[str, ...]) -> bool:
    haystack = f"{finding.topic}\n{finding.memo_text}\n{finding.context}"
    return any(keyword in haystack for keyword in keywords)


def sector_title(sector_id: str) -> str:
    for candidate, title, _patterns in SECTOR_PATTERNS:
        if candidate == sector_id:
            return title
    return sector_id


def unique_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def reviewable_text(doc: Document) -> str:
    excluded = {"notice"}
    pieces = [sector.text for sector_id, sector in doc.sectors.items() if sector_id not in excluded]
    return "\n".join(pieces) if pieces else doc.text


def section_after(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        return text[start:]
    return text[start:end]


def section_between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        return ""
    return text[start:end]


ROW_LABELS = [
    "평가 종류",
    "반영비율",
    "시기/영역",
    "영역 만점",
    "논술형 평가",
    "성취기준",
    "평가 요소",
    "평가 시기",
]


def row_segment(section: str, label: str) -> str:
    start = section.find(label)
    if start < 0:
        return ""
    end_candidates = []
    for other in ROW_LABELS:
        if other == label:
            continue
        idx = section.find(other, start + len(label))
        if idx >= 0:
            end_candidates.append(idx)
    end = min(end_candidates) if end_candidates else len(section)
    return section[start:end]


def row_text_cells(section: str, label: str) -> list[str]:
    segment = row_segment(section, label)
    if not segment:
        return []
    parts = [clean_cell(part) for part in segment.split("|")]
    cells = []
    for part in parts:
        if not part or label in part or set(part) <= {"-", ":"}:
            continue
        if part == "**":
            continue
        cells.append(part)
    return cells


def row_code_groups(section: str, label: str) -> list[set[str]]:
    cells = row_text_cells(section, label)
    groups = []
    for cell in cells:
        codes = set(CODE_RE.findall(cell))
        if codes:
            groups.append(codes)
    return groups


def assessment_ratio_table_rows(doc: Document) -> list[list[str]]:
    rows = doc.table_rows
    for index, row in enumerate(rows):
        joined = " ".join(row)
        if "평가" in joined and "종류" in joined:
            end = min(len(rows), index + 14)
            for j in range(index + 1, min(len(rows), index + 25)):
                if "성취율" in " ".join(rows[j]):
                    end = j
                    break
            chunk = rows[index:end]
            if any("성취기준" in " ".join(r) for r in chunk) and any("평가 시기" in " ".join(r) for r in chunk):
                return chunk
    return []


def table_rows_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(row) for row in rows)


def table_rows_for_text(rows: list[list[str]], text: str) -> list[list[str]]:
    if not rows:
        return []
    compact_text = re.sub(r"\s+", "", text)
    matched: list[list[str]] = []
    for row in rows:
        cells = [clean_cell(cell) for cell in row if clean_cell(cell)]
        distinctive = [
            re.sub(r"\s+", "", cell)
            for cell in cells
            if len(re.sub(r"\s+", "", cell)) >= 4
        ]
        if not distinctive:
            continue
        hits = sum(1 for cell in distinctive[:4] if cell in compact_text)
        if hits >= min(2, len(distinctive)):
            matched.append(row)
    return matched


def table_row_text_cells(rows: list[list[str]], label: str) -> list[str]:
    compact_label = re.sub(r"\s+", "", label)
    for row in rows:
        compact_cells = [re.sub(r"\s+", "", cell) for cell in row]
        if any(compact_label in cell for cell in compact_cells):
            cells = []
            seen_label = False
            for cell in row:
                cleaned = clean_cell(cell)
                if not cleaned:
                    continue
                if not seen_label and compact_label in re.sub(r"\s+", "", cleaned):
                    seen_label = True
                    continue
                if seen_label and cleaned not in {"-", "**"}:
                    cells.append(cleaned)
            return cells
    return []


def table_row_code_groups(rows: list[list[str]], label: str) -> list[set[str]]:
    groups = []
    for cell in table_row_text_cells(rows, label):
        codes = set(CODE_RE.findall(cell))
        if codes:
            groups.append(codes)
    return groups


def regular_exam_code_groups_from_overview(doc: Document) -> list[set[str]]:
    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    rows = assessment_ratio_table_rows(doc)
    if rows:
        source_text = table_rows_text(rows)
        labels = table_row_text_cells(rows, "시기/영역")
        code_groups = table_row_code_groups(rows, "성취기준")
    else:
        source_text = section
        labels = row_text_cells(section, "시기/영역")
        code_groups = row_code_groups(section, "성취기준")

    compact_source = re.sub(r"\s+", "", source_text)
    if "1차" in compact_source and "2차" in compact_source and labels and code_groups:
        merged_groups: list[set[str]] = []
        for exam_label in ("1차", "2차"):
            merged: set[str] = set()
            for label, codes in zip(labels, code_groups):
                if exam_label in re.sub(r"\s+", "", label):
                    merged.update(codes)
            if merged:
                merged_groups.append(merged)
        if len(merged_groups) >= 2:
            return merged_groups[:2]

    return code_groups[:2]


def clean_cell(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("*", " ")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 100):
        candidate = path.with_name(f"{path.stem}{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}_latest{path.suffix}")


def clear_directory_contents(folder: Path) -> None:
    for child in folder.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except PermissionError:
            # The app can still create a fresh numbered copy for locked HWPX files.
            continue


def monthly_codes_for_period(text: str, period: str, table_rows: list[list[str]] | None = None) -> set[str]:
    if "수시" in period or "중" in period:
        return set()
    week_keys = parse_period_weeks(period)
    if week_keys and table_rows:
        week_map = monthly_week_code_map(table_rows)
        codes: set[str] = set()
        for key in week_keys:
            codes.update(week_map.get(key, set()))
        if codes:
            return codes

    if week_keys:
        months = sorted({month for month, _week in week_keys})
    else:
        months = [int(month) for month in re.findall(r"([3-9]|1[0-2])\s*월", period)]
    if not months:
        return set()

    plan_text = text
    cut = plan_text.find("1. 평가의 목적")
    if cut >= 0:
        plan_text = plan_text[:cut]

    codes: set[str] = set()
    for month in months:
        month_block = monthly_block(plan_text, month)
        codes.update(CODE_RE.findall(month_block))
    return codes


def learned_codes_before_or_by_period(text: str, period: str, table_rows: list[list[str]] | None = None) -> set[str]:
    if "수시" in period or "중" in period:
        return set()
    week_keys = parse_period_weeks(period)
    if week_keys and table_rows:
        week_map = monthly_week_code_map(table_rows)
        if week_map:
            cutoff = max(week_keys)
            codes: set[str] = set()
            for key, key_codes in week_map.items():
                if key <= cutoff:
                    codes.update(key_codes)
            return codes
    return monthly_codes_for_period(text, period, table_rows)


def monthly_block(plan_text: str, month: int) -> str:
    pattern = re.compile(rf"(?:^|\s|\*){month}\s*월(?:\s|\*|$)")
    match = pattern.search(plan_text)
    if not match:
        return ""
    next_matches = []
    for next_month in range(month + 1, 13):
        next_match = re.search(rf"(?:^|\s|\*){next_month}\s*월(?:\s|\*|$)", plan_text[match.end():])
        if next_match:
            next_matches.append(match.end() + next_match.start())
    end = min(next_matches) if next_matches else len(plan_text)
    return plan_text[match.start():end]


def parse_period_weeks(period: str) -> list[tuple[int, int]]:
    compact = re.sub(r"\s+", "", period)
    if not compact or "수시" in compact or "중" in compact:
        return []

    cross_month = re.search(r"([3-9]|1[0-2])월([1-5])주~([3-9]|1[0-2])월([1-5])주", compact)
    if cross_month:
        start_month, start_week, end_month, end_week = map(int, cross_month.groups())
        return expand_week_range(start_month, start_week, end_month, end_week)

    explicit_pairs = [(int(month), int(week)) for month, week in re.findall(r"([3-9]|1[0-2])월([1-5])주", compact)]
    if len(explicit_pairs) > 1:
        return explicit_pairs

    same_month = re.search(r"([3-9]|1[0-2])월([1-5])(?:~([1-5]))?주", compact)
    if same_month:
        month = int(same_month.group(1))
        start = int(same_month.group(2))
        end = int(same_month.group(3) or start)
        if start <= end:
            return [(month, week) for week in range(start, end + 1)]
        return [(month, start)]

    return []


def expand_week_range(start_month: int, start_week: int, end_month: int, end_week: int) -> list[tuple[int, int]]:
    keys: list[tuple[int, int]] = []
    for month in range(start_month, end_month + 1):
        first_week = start_week if month == start_month else 1
        last_week = end_week if month == end_month else 5
        for week in range(first_week, last_week + 1):
            keys.append((month, week))
    return keys


def monthly_week_code_map(rows: list[list[str]]) -> dict[tuple[int, int], set[str]]:
    result: dict[tuple[int, int], set[str]] = {}
    current_month: int | None = None
    for row in rows:
        joined = " ".join(row)
        if "평가의 목적" in joined:
            break
        if not CODE_RE.search(joined):
            continue

        numbers: list[int] = []
        explicit_weeks: list[int] = []
        for cell_index, cell in enumerate(row[:4]):
            cleaned = clean_cell(cell)
            if re.fullmatch(r"[0-9]+", cleaned):
                if cell_index == 0 and current_month is not None and len(row) < 7 and 1 <= int(cleaned) <= 5:
                    explicit_weeks.append(int(cleaned))
                    continue
                numbers.append(int(cleaned))
            elif month_match := re.fullmatch(r"([3-9]|1[0-2])\s*월", cleaned):
                numbers.append(int(month_match.group(1)))
            elif re.fullmatch(r"[1-5]\s*주", cleaned):
                numbers.append(int(cleaned[0]))
            else:
                week_range = re.fullmatch(r"([1-5])\s*~\s*([1-5])", cleaned)
                if week_range:
                    start, end = map(int, week_range.groups())
                    if start <= end:
                        explicit_weeks.extend(range(start, end + 1))
                week_single = re.fullmatch(r"([1-5])\s*주?", cleaned)
                if week_single:
                    explicit_weeks.append(int(week_single.group(1)))

        month: int | None = None
        week: int | None = None
        for number in numbers:
            if month is None and 3 <= number <= 12:
                month = number
                current_month = number
            elif week is None and 1 <= number <= 5:
                week = number

        if month is None:
            month = current_month
            if explicit_weeks:
                week = explicit_weeks[0]
            else:
                for number in numbers:
                    if 1 <= number <= 5:
                        week = number
                        break

        codes = set(CODE_RE.findall(joined))
        if month and explicit_weeks:
            for explicit_week in explicit_weeks:
                result.setdefault((month, explicit_week), set()).update(codes)
        elif month and week:
            result.setdefault((month, week), set()).update(codes)
    return result


def monthly_week_text_map(rows: list[list[str]]) -> dict[tuple[int, int], str]:
    result: dict[tuple[int, int], list[str]] = {}
    current_month: int | None = None
    for row in rows:
        joined = " ".join(clean_cell(cell) for cell in row if clean_cell(cell))
        if not joined:
            continue
        if "평가의 목적" in joined:
            break

        numbers: list[int] = []
        explicit_weeks: list[int] = []
        for cell_index, cell in enumerate(row[:4]):
            cleaned = clean_cell(cell)
            if re.fullmatch(r"[0-9]+", cleaned):
                if cell_index == 0 and current_month is not None and len(row) < 7 and 1 <= int(cleaned) <= 5:
                    explicit_weeks.append(int(cleaned))
                    continue
                numbers.append(int(cleaned))
            elif month_match := re.fullmatch(r"([3-9]|1[0-2])\s*월", cleaned):
                numbers.append(int(month_match.group(1)))
            elif re.fullmatch(r"[1-5]\s*주", cleaned):
                numbers.append(int(cleaned[0]))
            else:
                week_range = re.fullmatch(r"([1-5])\s*~\s*([1-5])", cleaned)
                if week_range:
                    start, end = map(int, week_range.groups())
                    if start <= end:
                        explicit_weeks.extend(range(start, end + 1))
                week_single = re.fullmatch(r"([1-5])\s*주?", cleaned)
                if week_single:
                    explicit_weeks.append(int(week_single.group(1)))

        month: int | None = None
        week: int | None = None
        for number in numbers:
            if month is None and 3 <= number <= 12:
                month = number
                current_month = number
            elif week is None and 1 <= number <= 5:
                week = number

        if month is None:
            month = current_month
            if explicit_weeks:
                week = explicit_weeks[0]
            else:
                for number in numbers:
                    if 1 <= number <= 5:
                        week = number
                        break

        if month and explicit_weeks:
            for explicit_week in explicit_weeks:
                result.setdefault((month, explicit_week), []).append(joined)
        elif month and week:
            result.setdefault((month, week), []).append(joined)
    return {key: "\n".join(value) for key, value in result.items()}


def period_has_assessment_linkage(plan_text: str, period: str, rows: list[list[str]] | None = None, area_name: str = "") -> bool:
    period_text = monthly_text_for_period(plan_text, period, rows)
    if not period_text:
        return True
    if has_assessment_linkage_text(period_text, area_name):
        return True
    nearby_text = nearby_monthly_text_for_period(period, rows)
    if nearby_text and has_assessment_linkage_text(nearby_text, area_name):
        return True
    month_text = monthly_text_for_period(plan_text, period, None)
    if month_text and has_assessment_linkage_text(month_text, area_name):
        return True
    return False


def has_assessment_linkage_text(text: str, area_name: str = "") -> bool:
    compact = re.sub(r"\s+", "", text)
    area_compact = normalize_area_name(area_name)
    if area_compact and area_compact in normalize_area_name(text):
        return True
    keywords = (
        "수행평가",
        "수행평가연계",
        "수행평가연동",
        "수형평가",
        "수행연계",
        "평가",
        "평가연계",
        "논술",
        "논술형평가",
        "프로젝트",
        "발표",
        "구술",
        "토의",
        "토론",
        "실기",
        "실습",
        "포트폴리오",
        "보고서",
        "쓰기",
        "제작",
        "관찰",
        "기록",
        "작품",
        "활동지",
    )
    return any(keyword in compact for keyword in keywords)


def nearby_monthly_text_for_period(period: str, rows: list[list[str]] | None = None) -> str:
    if not rows:
        return ""
    week_keys = parse_period_weeks(period)
    if not week_keys:
        return ""
    text_map = monthly_week_text_map(rows)
    nearby_keys: list[tuple[int, int]] = []
    for month, week in week_keys:
        for nearby_week in (week - 1, week, week + 1):
            if 1 <= nearby_week <= 5:
                nearby_keys.append((month, nearby_week))
    chunks = [text_map.get(key, "") for key in nearby_keys]
    return "\n".join(chunk for chunk in chunks if chunk)


def monthly_text_for_period(text: str, period: str, rows: list[list[str]] | None = None) -> str:
    if "수시" in period or "중" in period:
        return ""
    week_keys = parse_period_weeks(period)
    if week_keys and rows:
        text_map = monthly_week_text_map(rows)
        chunks = [text_map.get(key, "") for key in week_keys]
        combined = "\n".join(chunk for chunk in chunks if chunk)
        if combined:
            return combined

    months = [int(month) for month in re.findall(r"([3-9]|1[0-2])\s*월", period)]
    if not months:
        return ""
    plan_text = text
    cut = plan_text.find("1. 평가의 목적")
    if cut >= 0:
        plan_text = plan_text[:cut]
    return "\n".join(monthly_block(plan_text, month) for month in months)


def split_performance_blocks(section: str) -> list[str]:
    markers = list(re.finditer(r"(?:^|\n)\s*(?:[가-하]\.|[0-9]+\.)\s*[^\n]{2,60}", section))
    if not markers:
        return [section]
    blocks = []
    for i, marker in enumerate(markers):
        start = marker.start()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(section)
        block = section[start:end]
        if "평가" in block or "성취기준" in block:
            blocks.append(block)
    return blocks or [section]


def extract_rubric_rows(block: str) -> list[tuple[str, list[int], bool]]:
    # Finds score ladders around phrases such as "채점 기준을 4개 만족한 경우 7".
    if block.count("평가영역명") > 1:
        rows: list[tuple[str, list[int], bool]] = []
        for part in re.split(r"(?=평가영역명)", block):
            if part and part != block:
                rows.extend(extract_rubric_rows(part))
        return rows

    rows: list[tuple[str, list[int], bool]] = []
    pattern = re.compile(r"(채점\s*기준[^|]{0,80}만족[^|]{0,80})\|\s*(\d{1,2})\s*(?=\|)")
    matches = list(pattern.finditer(block))
    if matches:
        scores = [int(m.group(2)) for m in matches]
        for run in descending_runs(scores):
            if len(run) >= 4:
                rows.append((str(run[min(2, len(run) - 1)]), run, True))

    # Fallback for compact table text: collect descending score runs.
    numbers = [int(n) for n in re.findall(r"(?<![0-9])([1-9]|10|20|30)(?![0-9])", block)]
    run: list[int] = []
    for n in numbers:
        if not run or n < run[-1]:
            run.append(n)
        else:
            if len(run) >= 4 and max(run) <= 30:
                rows.append((str(run[min(2, len(run) - 1)]), run, False))
            run = [n]
    if len(run) >= 4 and max(run) <= 30:
        rows.append((str(run[min(2, len(run) - 1)]), run, False))
    return rows


def descending_runs(scores: list[int]) -> list[list[int]]:
    if not scores:
        return []
    runs: list[list[int]] = []
    run = [scores[0]]
    for score in scores[1:]:
        if score < run[-1]:
            run.append(score)
        else:
            runs.append(run)
            run = [score]
    runs.append(run)
    return runs


def extract_basic_score_rows(section: str) -> list[tuple[int, int, str, str]]:
    rows: list[tuple[int, int, str, str]] = []
    markers = performance_text_score_markers(section)
    for index, (start, full_score) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(section)
        block = section[start:end]
        explicit_basic = extract_overall_basic_score_from_block(block)
        if explicit_basic is not None:
            rows.append((full_score, explicit_basic, block[:1600], "기본점수"))
    return rows


def performance_text_score_markers(section: str) -> list[tuple[int, int]]:
    markers: list[tuple[int, int]] = []
    patterns = [
        r"평가\s*영역명[\s\S]{0,220}?영역\s*만점\s*(\d{1,3})\s*점?",
        r"평가영역명[\s\S]{0,220}?영역만점\s*(\d{1,3})\s*점?",
        r"평가영역명[\s\S]{0,160}?\((\d{1,3})점\)",
        r"(?:^|\n)\s*(?:[가-하]\.|[0-9]+\.)\s*[^\n]{2,80}?\((\d{1,3})점\)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, section):
            score = int(match.group(1))
            if score <= 0:
                continue
            markers.append((match.start(), score))

    markers.sort(key=lambda item: item[0])
    deduped: list[tuple[int, int]] = []
    for start, score in markers:
        if deduped and abs(start - deduped[-1][0]) < 30:
            continue
        deduped.append((start, score))
    return deduped


def extract_performance_text_detail_items(section: str) -> list[tuple[str, int, str]]:
    if not section:
        return []
    items: list[tuple[str, int, str]] = []
    pattern = re.compile(
        r"평가\s*영역명\s*(?P<name>[\s\S]{2,120}?)\s*영역\s*만점\s*(?P<score>\d{1,3})\s*점?"
    )
    matches = list(pattern.finditer(section))
    for index, match in enumerate(matches):
        name = clean_cell(match.group("name"))
        name = re.sub(r"^(?:[가-하]\.|[0-9]+\.)\s*", "", name).strip()
        score = int(match.group("score"))
        if not normalize_area_name(name) or score <= 0:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(section), match.end() + 1600)
        context = section[match.start() : end][:1800]
        items.append((name, score, context))
    return items


def extract_overall_basic_score_from_block(block: str) -> int | None:
    cleaned = clean_cell(block)
    candidates: list[tuple[int, int, int]] = []
    strong_patterns = [
        r"(?:모든\s*)?(?:항목|평가\s*요소|평가요소)[^0-9]{0,140}?(?:모두\s*)?(?:만족하지\s*못|만족하지\s*않|어느\s*것도\s*만족하지\s*않)[^0-9]{0,100}?\(?\s*기본점수\s*\)?\s*(?:\||\s)+(\d{1,3})",
        r"평가\s*요소\s*중\s*어느\s*것도[^0-9]{0,140}?\(?\s*기본점수\s*\)?\s*(?:\||\s)+(\d{1,3})",
        r"기본점수\s*\(\s*백지[^)]{0,40}포함\s*\)\s*(?:\||\s)+(\d{1,3})(?=\s*(?:\||자발|미참여|미응시|장기|미인정|$))",
        r"기본점수\s*\(\s*백지[^)]{0,40}\)\s*(?:\||\s)+(\d{1,3})(?=\s*(?:\||자발|미참여|미응시|장기|미인정|$))",
    ]
    table_patterns = [
        r"기본점수\s*\)\s*(?:\||\s)+(\d{1,3})(?=\s*(?:\||자발|백지|미참여|미응시|장기|미인정|$))",
        r"(?:^|\|)\s*기본점수\s*(?:\||\s)+(\d{1,3})(?=\s*(?:\||자발|백지|미참여|미응시|장기|미인정|$))",
        r"기본점수\s*(?:\||\s)+(\d{1,3})(?=\s*(?:\||자발|백지|미참여|미응시|장기|미인정|$))",
    ]
    for priority, patterns in ((3, strong_patterns), (2, table_patterns)):
        for pattern in patterns:
            for match in re.finditer(pattern, cleaned):
                context_after = cleaned[match.end() : match.end() + 180]
                if priority < 3 and not re.search(r"자발|백지|미참여|미응시|장기|미인정", context_after):
                    continue
                candidates.append((priority, match.start(), int(match.group(1))))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2]
    return None


def performance_area_names_from_ratio(doc: Document) -> list[str]:
    items = assessment_overview_items(doc)
    if items:
        return [
            item.name
            for item in items
            if (item.score is not None or item.ratio is not None) and is_valid_performance_area_name(item.name)
        ]
    rows = assessment_ratio_table_rows(doc)
    names = performance_area_names_from_ratio_rows(rows)
    if names:
        return names
    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    return performance_area_names_from_ratio_text(section)


def assessment_overview_items(doc: Document) -> list[AssessmentOverviewItem]:
    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    row_items = assessment_overview_items_from_rows(assessment_ratio_table_rows(doc))
    text_items = assessment_overview_items_from_text(section)
    if row_items and (any(item.period for item in row_items) or not text_items):
        return row_items
    return text_items or row_items


def match_overview_item_by_name(name: str, overview_by_name: dict[str, AssessmentOverviewItem]) -> AssessmentOverviewItem | None:
    normalized = normalize_area_name(name)
    if not normalized:
        return None
    if normalized in overview_by_name:
        return overview_by_name[normalized]
    for overview_name, item in overview_by_name.items():
        if normalized in overview_name or overview_name in normalized:
            return item
    name_tokens = set(re.findall(r"[가-힣A-Za-z0-9]+", normalized))
    best: tuple[int, AssessmentOverviewItem] | None = None
    for overview_name, item in overview_by_name.items():
        overview_tokens = set(re.findall(r"[가-힣A-Za-z0-9]+", overview_name))
        overlap = len(name_tokens & overview_tokens)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, item)
    return best[1] if best else None


def assessment_overview_items_from_text(section: str) -> list[AssessmentOverviewItem]:
    if not section:
        return []
    names = performance_area_names_from_ratio_text(section)
    if not names:
        return []
    all_pairs = score_ratio_pairs_from_text(section)
    regular_count = regular_exam_score_column_count(section, all_pairs)
    pairs = all_pairs[regular_count:] if regular_count else all_pairs
    if pairs:
        names = coalesce_area_name_fragments(names, len(pairs))
    periods = periods_from_overview_text(section, len(names))
    codes = tuple(sorted(set(CODE_RE.findall(section))))
    paired = pairs[-len(names):] if len(pairs) >= len(names) else []
    items: list[AssessmentOverviewItem] = []
    for index, name in enumerate(names):
        score: int | None = None
        ratio: float | None = None
        if paired:
            score, ratio = paired[index]
        period = periods[-len(names) + index] if len(periods) >= len(names) else ""
        confidence = "높음" if paired else "확인 필요"
        items.append(AssessmentOverviewItem(name, ratio, score, codes, period, section[:1800], confidence))
    return items


def regular_exam_score_column_count(section: str, pairs: list[tuple[int, float]]) -> int:
    if not pairs or "정기시험" not in section:
        return 0
    compact = re.sub(r"\s+", "", section)
    if "선택형" in compact and "논술형" in compact:
        return min(2, len(pairs))
    return 1


def regular_exam_items_from_overview(doc: Document) -> list[AssessmentOverviewItem]:
    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    rows = assessment_ratio_table_rows(doc)
    source_text = table_rows_text(rows) if rows else section
    if "정기시험" not in source_text:
        return []

    pairs = score_ratio_pairs_from_text(source_text)
    if not pairs:
        pairs = score_ratio_pairs_from_rows(rows)
    count = 0
    compact = re.sub(r"\s+", "", source_text)
    if "1차" in compact and "2차" in compact:
        count = 2
    elif "정기시험" in compact:
        count = 1
    result: list[AssessmentOverviewItem] = []
    for index, (score, ratio) in enumerate(pairs[:count]):
        result.append(
            AssessmentOverviewItem(
                f"{index + 1}차 정기시험" if count > 1 else "정기시험",
                ratio,
                score,
                tuple(),
                "",
                source_text[:1800],
                "확인 필요",
            )
        )
    return result


def score_ratio_pairs_from_rows(rows: list[list[str]]) -> list[tuple[int, float]]:
    pairs: list[tuple[int, float]] = []
    for cell in table_row_text_cells(rows, "영역 만점"):
        for score, ratio in re.findall(r"(\d{1,3})\s*점\s*\(?\s*(\d+(?:\.\d+)?)\s*%\s*\)?", cell):
            score_int = int(score)
            ratio_float = float(ratio)
            if score_int == 100 and ratio_float == 100:
                continue
            pairs.append((score_int, ratio_float))
    if pairs:
        return pairs
    source_text = table_rows_text(rows)
    return score_ratio_pairs_from_text(source_text)


def essay_ratio_from_overview(doc: Document) -> float | None:
    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    rows = assessment_ratio_table_rows(doc)
    source_text = table_rows_text(rows) if rows else section
    if "논술" not in source_text and "구술" not in source_text:
        return None

    cells = table_row_text_cells(rows, "논술형 평가") if rows else row_text_cells(section, "논술형 평가")
    values: list[float] = []
    for cell in cells:
        values.extend(float(value) for value in PERCENT_RE.findall(cell))
    if values:
        values = [value for value in values if value != 100]
        return sum(values) if values else None

    for line in source_text.splitlines():
        if "논술" not in line and "구술" not in line:
            continue
        values = [float(value) for value in PERCENT_RE.findall(line)]
        if values:
            values = [value for value in values if value != 100]
            if values:
                return sum(values)
    return None


def is_pe_arts_subject(subject: str | None) -> bool:
    normalized = normalize_area_name(subject or "")
    return normalized in {"체육", "음악", "미술", "예술"}


def achievement_code_range_contexts(text: str) -> list[str]:
    contexts: list[str] = []
    pattern = re.compile(r"\[[0-9][^\]\s]{1,20}\]\s*[~∼-]\s*\[[0-9][^\]\s]{1,20}\]")
    for match in pattern.finditer(text):
        start = max(0, match.start() - 120)
        end = min(len(text), match.end() + 120)
        contexts.append(text[start:end])
    return contexts


def is_consulting_period_format(period: str) -> bool:
    compact = re.sub(r"\s+", "", clean_cell(period))
    if not compact:
        return True
    if "수시" in compact or "학기중" in compact:
        return False
    return bool(re.search(r"(?:[3-9]|1[0-2])월[1-5]주", compact))


def is_vague_performance_area_name(name: str) -> bool:
    compact = re.sub(r"\s+", "", clean_cell(name))
    if compact in {
        "쓰기",
        "말하기",
        "듣기",
        "읽기",
        "발표",
        "보고서",
        "논술",
        "논술형",
        "포트폴리오",
        "프로젝트",
        "탐구",
        "실기",
        "실습",
        "감상",
        "표현",
        "실험",
    }:
        return True
    if len(compact) <= 3 and re.search(r"(쓰기|발표|보고서|논술|탐구|실기|감상|표현)$", compact):
        return True
    return False


def coalesce_area_name_fragments(names: list[str], target_count: int) -> list[str]:
    names = [name for name in names if is_valid_performance_area_name(name)]
    if target_count <= 0 or len(names) <= target_count:
        return names

    generic_suffixes = (
        "만들기",
        "발표하기",
        "쓰기",
        "탐구",
        "주제탐구",
        "논술",
        "논술하기",
        "보고서",
        "프로젝트",
        "포트폴리오",
    )
    merged = names[:]
    while len(merged) > target_count:
        merge_index: int | None = None
        for index in range(len(merged) - 1):
            current = normalize_area_name(merged[index])
            nxt = normalize_area_name(merged[index + 1])
            if len(current) <= 6 and (len(nxt) <= 8 or any(nxt.endswith(suffix) for suffix in generic_suffixes)):
                merge_index = index
                break
            if any(nxt == normalize_area_name(suffix) or nxt.endswith(normalize_area_name(suffix)) for suffix in generic_suffixes):
                merge_index = index
                break
        if merge_index is None:
            merge_index = len(merged) - 2
        merged[merge_index] = f"{merged[merge_index]} {merged[merge_index + 1]}".strip()
        del merged[merge_index + 1]
    return merged


def assessment_overview_items_from_rows(rows: list[list[str]]) -> list[AssessmentOverviewItem]:
    names = performance_area_names_from_ratio_rows(rows)
    if not names:
        return []
    score_cells = table_row_text_cells(rows, "영역 만점")
    pairs: list[tuple[int, float]] = []
    for cell in score_cells:
        match = re.search(r"(\d{1,3})\s*점\s*\(?\s*(\d+(?:\.\d+)?)\s*%\s*\)?", cell)
        if match:
            score, ratio = match.groups()
            pairs.append((int(score), float(ratio)))
            continue
        score_match = re.search(r"(\d{1,3})\s*점", cell)
        if score_match:
            pairs.append((int(score_match.group(1)), None))  # type: ignore[arg-type]
    pairs = [(score, ratio) for score, ratio in pairs if score != 100 and ratio != 100]
    pairs = pairs[-len(names):] if len(pairs) >= len(names) else []
    periods = table_row_text_cells(rows, "평가 시기")
    codes = tuple(sorted(set(CODE_RE.findall(table_rows_text(rows)))))
    items: list[AssessmentOverviewItem] = []
    for index, name in enumerate(names):
        score: int | None = None
        ratio: float | None = None
        if pairs:
            score, ratio = pairs[index]
        period = periods[-len(names) + index] if len(periods) >= len(names) else ""
        items.append(AssessmentOverviewItem(name, ratio, score, codes, period, table_rows_text(rows[:12]), "높음" if pairs else "확인 필요"))
    return items


def score_ratio_pairs_from_text(section: str) -> list[tuple[int, float]]:
    segment = section
    start = segment.find("영역 만점")
    if start >= 0:
        segment = segment[start:]
    end_candidates = [idx for marker in ("논술형 평가", "성취기준", "평가요소", "평가 시기") if (idx := segment.find(marker)) > 0]
    if end_candidates:
        segment = segment[: min(end_candidates)]
    pairs: list[tuple[int, float]] = []
    for score, ratio in re.findall(r"(\d{1,3})\s*점\s*\(?\s*(\d+(?:\.\d+)?)\s*%\s*\)?", segment):
        score_int = int(score)
        ratio_float = float(ratio)
        if score_int == 100 and ratio_float == 100:
            continue
        pairs.append((score_int, ratio_float))
    return pairs


def periods_from_overview_text(section: str, performance_count: int | None = None) -> list[str]:
    start = section.find("평가 시기")
    if start < 0:
        return []
    segment = section[start:]
    lines = [clean_cell(line) for line in segment.splitlines()]
    lines = [line for line in lines if line and line != "평가 시기"]
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in lines
        if re.search(r"(?:[3-9]|1[0-2])\s*월\s*[1-5]\s*주|수시|학기\s*중", line)
    ]
    if not lines:
        return []

    periods: list[str] = []
    current = ""
    for line in lines:
        if current:
            current = f"{current} {line}".strip()
        else:
            current = line
        compact = re.sub(r"\s+", "", current)
        if compact.endswith(("~", "∼", ",")):
            continue
        periods.append(current)
        current = ""
    if current:
        periods.append(current)

    if performance_count is not None and performance_count > 0:
        target = performance_count + (1 if "정기시험" in section else 0)
        if len(periods) > target:
            periods = periods[: target - 1] + [" ".join(periods[target - 1 :])]
    return periods


def performance_area_names_from_ratio_rows(rows: list[list[str]]) -> list[str]:
    names = table_row_text_cells(rows, "시기/영역") or table_row_text_cells(rows, "영역")
    if not names:
        return []
    source_text = table_rows_text(rows)
    skip = 0
    if "정기시험" in source_text:
        skip = 2 if "1차" in source_text and "2차" in source_text else 1
    result = []
    for name in names[skip:]:
        if is_assessment_type_cell(name) and result:
            break
        if not is_valid_performance_area_name(name):
            continue
        result.append(name)
    return result


def performance_area_names_from_ratio_text(section: str) -> list[str]:
    if not section:
        return []
    lines = [clean_cell(line) for line in section.splitlines()]
    lines = [line for line in lines if line]
    start = next((i for i, line in enumerate(lines) if line in {"영역", "시기/영역"} or "시기/영역" in line), -1)
    if start < 0:
        return []
    result: list[str] = []
    for line in lines[start + 1 :]:
        compact = re.sub(r"\s+", "", line)
        if compact.startswith(("성취기준", "반영비율", "논술형평가", "평가요소", "MYP", "평가시기")):
            break
        if is_assessment_type_cell(line) and result:
            break
        if not is_valid_performance_area_name(line):
            continue
        if line.startswith("(") and result:
            result[-1] = f"{result[-1]} {line}"
        else:
            result.append(line)
    return [name for name in result if normalize_area_name(name)]


def is_assessment_type_cell(value: str) -> bool:
    compact = re.sub(r"\s+", "", clean_cell(value))
    return compact in {
        "선택형",
        "논술형",
        "서술형",
        "프로젝트형",
        "실기형",
        "실습형",
        "실기·실습",
        "실기실습",
        "구술발표",
        "구술·발표",
        "포트폴리오",
    }


def is_valid_performance_area_name(value: str) -> bool:
    cleaned = clean_cell(value)
    compact = re.sub(r"\s+", "", cleaned)
    if not compact or len(compact) < 2:
        return False
    invalid_exact = {
        "1차",
        "2차",
        "1회",
        "2회",
        "1차지필",
        "2차지필",
        "1차정기시험",
        "2차정기시험",
        "합계",
        "정기시험",
        "수행평가",
        "평가종류",
        "평가유형",
        "반영비율",
        "선택형",
        "논술형",
        "논술평가",
        "논술형평가",
        "발표",
        "보고서",
        "영역",
        "시기/영역",
        "영역/방법",
        "-",
    }
    if compact in invalid_exact or is_assessment_type_cell(cleaned):
        return False
    if any(label in compact for label in ("영역만점", "반영비율", "성취기준", "평가요소", "평가시기")):
        return False
    if re.fullmatch(r"(?:[12]차)?\(?\d+(?:\.\d+)?\(?%\)?\)?|\d+점|\d+", compact):
        return False
    if re.fullmatch(r"[12]차\(?\d+(?:\.\d+)?%\)?", compact):
        return False
    if CODE_RE.search(cleaned):
        return False
    return True


def performance_area_percentages_from_ratio(doc: Document) -> list[tuple[str, float]]:
    items = assessment_overview_items(doc)
    if items:
        return [(item.name, item.ratio) for item in items if item.ratio is not None]
    names = performance_area_names_from_ratio(doc)
    if not names:
        return []
    rows = assessment_ratio_table_rows(doc)
    percent_cells = table_row_text_cells(rows, "반영비율") if rows else []
    if not percent_cells:
        percent_cells = table_row_text_cells(rows, "논술형 평가") if rows else []
    if percent_cells:
        values: list[float] = []
        for cell in percent_cells:
            match = PERCENT_RE.search(cell)
            if match:
                values.append(float(match.group(1)))
        if values and values[-1] == 100:
            values = values[:-1]
        values = values[-len(names) :]
        return list(zip(names, values))

    section = sector_text(doc, "assessment_overview", "4. 평가의 종류", "5.")
    lines = [clean_cell(line) for line in section.splitlines() if clean_cell(line)]
    for label in ("반영비율", "논술형 평가", "논술형평가"):
        for index, line in enumerate(lines):
            if re.sub(r"\s+", "", label) not in re.sub(r"\s+", "", line):
                continue
            values: list[float] = []
            for next_line in lines[index + 1 : index + 1 + len(names) + 3]:
                values.extend(float(value) for value in PERCENT_RE.findall(next_line))
            if values:
                if values and values[-1] == 100:
                    values = values[:-1]
                values = values[-len(names) :]
                return list(zip(names, values))
    return []


def performance_area_scores_from_ratio(rows: list[list[str]], doc: Document | None = None) -> dict[str, int]:
    if doc is not None:
        items = assessment_overview_items(doc)
        if items:
            return {normalize_area_name(item.name): item.score for item in items if item.score is not None}
    names = table_row_text_cells(rows, "시기/영역")
    score_cells = table_row_text_cells(rows, "영역 만점")
    if not names or not score_cells:
        return {}

    source_text = table_rows_text(rows)
    skip = 0
    if "정기시험" in source_text:
        skip = 2 if "1차" in source_text and "2차" in source_text else 1

    names = [
        name
        for name in names[skip:]
        if name not in {"-", ""} and normalize_area_name(name) not in {"선택형", "논술형", "1차", "2차"}
    ]
    score_values: list[int] = []
    for score_cell in score_cells:
        match = re.search(r"(\d{1,3})\s*점", score_cell)
        if match:
            score_values.append(int(match.group(1)))
    if score_values and score_values[-1] == 100:
        score_values = score_values[:-1]
    score_values = score_values[-len(names) :] if names else []

    result: dict[str, int] = {}
    for name, score in zip(names, score_values):
        if score <= 0 or score > 50:
            continue
        result[normalize_area_name(name)] = score
    return result


def performance_detail_area_names(doc: Document) -> list[str]:
    if doc.table_rows:
        names = [str(block.get("name", "")) for block in performance_detail_blocks_from_tables(doc.table_rows)]
        names = [name for name in names if normalize_area_name(name)]
        if names:
            return names
    section = sector_text(doc, "performance_detail", "6. 수행평가", "7.")
    if not section:
        return []
    text_items = extract_performance_text_detail_items(section)
    if text_items:
        return [name for name, _score, _context in text_items if normalize_area_name(name)]
    names: list[str] = []
    for match in re.finditer(r"평가영역명\s*\n?\s*([^\n]{2,80}?\(\s*\d{1,3}\s*점\s*\))", section):
        names.append(match.group(1))
    if not names:
        for match in re.finditer(r"(?:^|\n)\s*(?:[가-힣]\.|[0-9]+\.)\s*([^\n]{2,80}?\(\s*[^)]*형\s*\))", section):
            names.append(match.group(1))
    return [name for name in names if normalize_area_name(name)]


def monthly_plan_text(text: str) -> str:
    cut_candidates = []
    for marker in ("1. 평가의 목적", "4. 평가의 종류", "5. 성취율", "6. 수행평가"):
        idx = text.find(marker)
        if idx > 0:
            cut_candidates.append(idx)
    if not cut_candidates:
        return ""
    return text[: min(cut_candidates)]


def extract_score_range_only_rows_from_tables(rows: list[list[str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for row in rows:
        joined = " | ".join(clean_cell(cell) for cell in row if clean_cell(cell))
        if not joined or "평가요소" in joined:
            continue
        for match in re.finditer(r"(?<![0-9])(\d{1,3})\s*[~∼-]\s*(\d{1,3})\s*점", joined):
            high, low = map(int, match.groups())
            if high > low:
                result.append((match.group(0), joined))
    return result


def extract_score_range_only_rows_from_text(section: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in re.finditer(r"(?<![0-9])(\d{1,3})\s*[~∼-]\s*(\d{1,3})\s*점", section):
        high, low = map(int, match.groups())
        if high <= low:
            continue
        start = max(0, match.start() - 250)
        end = min(len(section), match.end() + 250)
        result.append((match.group(0), section[start:end]))
    return result


def normalize_area_name(name: str) -> str:
    name = clean_cell(name)
    name = re.sub(r"\([^)]*논술형[^)]*\)", "", name)
    name = re.sub(r"\(\s*\d{1,3}\s*점\s*\)", "", name)
    name = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    return name


def match_ratio_score(detail_name: str, ratio_scores: dict[str, int]) -> int | None:
    normalized = normalize_area_name(detail_name)
    if normalized in ratio_scores:
        return ratio_scores[normalized]
    for name, score in ratio_scores.items():
        if name and (name in normalized or normalized in name):
            return score
    return None


def performance_detail_blocks_from_tables(rows: list[list[str]]) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        joined = " ".join(row)
        if row and "평가영역명" in row[0]:
            match = re.search(r"(.+?)\((\d{1,3})\s*점\)", joined)
            if match:
                if current:
                    blocks.append(current)
                current = {"name": match.group(1).replace("평가영역명", "").strip(), "score": int(match.group(2)), "rows": [row]}
                continue
        if row and "평가 영역명" in row[0]:
            name = row[1] if len(row) > 1 else ""
            score: int | None = None
            for index, cell in enumerate(row):
                if "영역만점" in cell and index + 1 < len(row):
                    match = re.search(r"\d{1,3}", row[index + 1])
                    if match:
                        score = int(match.group(0))
                        break
            if name and score is not None:
                if current:
                    blocks.append(current)
                current = {"name": name, "score": score, "rows": [row]}
                continue
        if current:
            current["rows"].append(row)  # type: ignore[index]
    if current:
        blocks.append(current)
    return blocks


def extract_basic_score_rows_from_tables(
    rows: list[list[str]], ratio_scores: dict[str, int] | None = None
) -> list[tuple[int, int, str, str]]:
    result: list[tuple[int, int, str, str]] = []
    ratio_scores = ratio_scores or {}
    for block in performance_detail_blocks_from_tables(rows):
        block_rows = block["rows"]  # type: ignore[assignment]
        detail_score = int(block["score"])
        full_score = match_ratio_score(str(block["name"]), ratio_scores) or detail_score
        for row in block_rows:
            joined = " ".join(row)
            if not is_overall_basic_score_row(row):
                continue
            if is_element_level_basic_score_row(row):
                continue

            numeric_cells: list[int] = []
            for cell in row:
                cleaned = clean_cell(cell)
                if re.fullmatch(r"\d{1,3}", cleaned):
                    numeric_cells.append(int(cleaned))

            if not numeric_cells:
                trailing = re.findall(r"(?<![0-9])(\d{1,3})(?![0-9])", joined)
                numeric_cells = [int(value) for value in trailing]

            candidates = [score for score in numeric_cells if score <= max(full_score, detail_score)]
            if not candidates:
                continue

            basic_score = candidates[-1]
            context = table_rows_text(block_rows)
            result.append((full_score, basic_score, context, str(basic_score)))

    return result


def is_element_level_basic_score_row(row: list[str]) -> bool:
    if not row:
        return False
    joined = clean_cell(" ".join(row))
    if "기본점수" not in joined:
        return False

    first = clean_cell(row[0])
    first_compact = re.sub(r"\s+", "", first)
    if first_compact.startswith(("기본점수", "(기본점수)", "평가요소중어느것도")):
        return False

    if evaluation_element_score_from_row(row)[0] is not None:
        return True
    if re.search(r"\(\s*\d{1,3}\s*점?\s*\)", first):
        return True
    if row_last_score(row) is not None and re.search(r"\d{1,3}\s*\(\s*기본점수\s*\)", joined):
        return True
    return False


def is_overall_basic_score_row(row: list[str]) -> bool:
    if not row:
        return False
    first = clean_cell(row[0]).replace(" ", "")
    if first.startswith("기본점수"):
        return True
    if first.startswith("(기본점수)") or first.startswith("기본점수("):
        return True
    joined = clean_cell(" ".join(row))
    compact = re.sub(r"\s+", "", joined)
    if "기본점수" not in compact:
        return False
    if re.search(r"(모든항목|모든평가요소|평가요소중어느것도|평가요소를모두)", compact) and re.search(
        r"(만족하지못|만족하지않)", compact
    ):
        return True
    return False


def extract_table_rubric_ladders(rows: list[list[str]]) -> list[dict[str, object]]:
    ladders: list[dict[str, object]] = []
    for block in performance_detail_blocks_from_tables(rows):
        block_rows = block["rows"]  # type: ignore[assignment]
        current_scores: list[int] = []
        current_rows: list[list[str]] = []

        def flush() -> None:
            nonlocal current_scores, current_rows
            if len(current_scores) >= 4:
                min_index = current_scores.index(min(current_scores))
                min_row = current_rows[min_index] if min_index < len(current_rows) else []
                score_index = min(2, len(current_scores) - 1)
                score_row = current_rows[score_index] if score_index < len(current_rows) else []
                top_row = current_rows[0] if current_rows else []
                element_score, element_name = evaluation_element_score_from_row(top_row)
                ladders.append(
                    {
                        "scores": current_scores[:],
                        "element_score": element_score,
                        "element_name": element_name,
                        "context": table_rows_text(current_rows),
                        "min_anchor": rubric_row_anchor(min_row),
                        "score_anchor": rubric_row_anchor(score_row),
                        "top_anchor": rubric_row_anchor(top_row),
                    }
                )
            current_scores = []
            current_rows = []

        in_score_table = False
        for row in block_rows:
            joined = " ".join(row)
            compact_joined = re.sub(r"\s+", "", joined)
            if ("평가요소" in compact_joined or "평가요소" in joined) and "배점" in joined:
                in_score_table = True
                flush()
                continue
            if not in_score_table:
                continue
            if "기본점수" in joined:
                if is_overall_basic_score_row(row):
                    flush()
                    continue
                score = row_last_score(row)
                if score is not None and current_scores and score <= current_scores[-1]:
                    current_scores.append(score)
                    current_rows.append(row)
                    flush()
                    continue
                flush()
                continue

            if (
                "본인의 의사" in joined
                or "학업성적관리규정" in joined
                or "평가요소" in joined
                and "관련 성취기준" in joined
            ):
                flush()
                break

            score = row_last_score(row)
            if score is None:
                continue
            starts_new = bool(current_scores and score > current_scores[-1])
            if starts_new and current_scores:
                flush()
            current_scores.append(score)
            current_rows.append(row)
        flush()
    return ladders


def row_last_score(row: list[str]) -> int | None:
    for cell in reversed(row):
        cleaned = clean_cell(cell)
        if re.fullmatch(r"\d{1,3}", cleaned):
            return int(cleaned)
        match = re.match(r"\s*(\d{1,3})\s*\(", cleaned)
        if match:
            return int(match.group(1))
    return None


def evaluation_element_score_from_row(row: list[str]) -> tuple[int | None, str]:
    if not row:
        return None, ""
    first = clean_cell(row[0])
    match = re.search(r"(.{1,80}?)\(\s*(\d{1,3})\s*점\s*\)", first)
    if not match:
        return None, first
    return int(match.group(2)), clean_cell(match.group(1))


def extract_text_element_score_mismatches(section: str) -> list[tuple[str, int, int, str]]:
    lines = [clean_cell(line) for line in section.splitlines()]
    lines = [line for line in lines if line]
    result: list[tuple[str, int, int, str]] = []
    current_name: str | None = None
    current_score: int | None = None
    current_scores: list[int] = []
    current_context: list[str] = []
    seen_keys: set[tuple[str, int, int]] = set()

    def is_element_start(index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        name = lines[index]
        if len(name) > 80 or len(name) < 2:
            return False
        compact_name = re.sub(r"\s+", "", name)
        if compact_name in {"평가요소", "채점기준", "배점", "기본점수", "영역만점", "반영비율", "평가종류", "평가영역명"}:
            return False
        if compact_name.startswith(
            (
                "평가영역명",
                "교육과정",
                "성취기준",
                "평가기준",
                "평가방법",
                "평가시기",
                "반영비율",
                "영역만점",
                "자발적",
                "장기",
                "본인의",
                "백지",
                "평가요소중",
                "학업성적관리규정",
                "논술형평가",
            )
        ):
            return False
        if "%" in compact_name or re.fullmatch(r"\(?\d{1,3}\s*(?:점|%)?\)?", compact_name):
            return False
        if len(CODE_RE.findall(name)) > 0:
            return False
        return re.fullmatch(r"\(?\s*\d{1,3}\s*점\s*\)?", lines[index + 1]) is not None

    def line_score(line: str) -> int | None:
        match = re.fullmatch(r"(\d{1,3})(?:\s*\([^)]*기본점수[^)]*\))?", line)
        if not match:
            return None
        return int(match.group(1))

    def flush() -> None:
        nonlocal current_name, current_score, current_scores, current_context
        if current_name and current_score and current_scores:
            max_score = max(current_scores)
            key = (current_name, current_score, max_score)
            # HWPX text extraction often interleaves side-by-side rubric columns.
            # Treat a mismatch as reliable only when the element score itself
            # appears in the same score ladder; otherwise leave it to table-based
            # parsing or human confirmation instead of creating a false error.
            high_confidence = current_score in current_scores and max_score <= current_score + 5
            if high_confidence and max_score != current_score and key not in seen_keys:
                seen_keys.add(key)
                result.append((current_name, current_score, max_score, "\n".join(current_context[:18])))
        current_name = None
        current_score = None
        current_scores = []
        current_context = []

    index = 0
    while index < len(lines):
        if is_element_start(index):
            flush()
            current_name = lines[index]
            current_score = int(re.search(r"\d{1,3}", lines[index + 1]).group(0))  # type: ignore[union-attr]
            current_context = [lines[index], lines[index + 1]]
            index += 2
            continue
        if current_name:
            compact_line = re.sub(r"\s+", "", lines[index])
            if (
                is_element_start(index)
                or compact_line.startswith(
                    (
                        "기본점수",
                        "자발적",
                        "장기",
                        "본인의",
                        "백지",
                        "평가요소중",
                        "모든항목",
                        "모든평가요소",
                        "학업성적관리규정",
                    )
                )
            ):
                flush()
                if is_element_start(index):
                    continue
                index += 1
                continue
            score = line_score(lines[index])
            if score is not None:
                current_scores.append(score)
            if len(current_context) < 25:
                current_context.append(lines[index])
        index += 1
    flush()
    return result


def comparable_interval_scores(scores: list[int]) -> list[int]:
    if len(scores) < 2:
        return scores
    collapsed = scores[:]
    while len(collapsed) >= 2 and collapsed[-1] == collapsed[-2]:
        collapsed.pop()
    if len(collapsed) != len(set(collapsed)):
        return []
    return collapsed


def rubric_row_anchor(row: list[str]) -> str:
    cleaned = [clean_cell(cell) for cell in row if clean_cell(cell)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    # Prefer the criterion text immediately before the score cell.
    return cleaned[-2] if re.fullmatch(r"\d{1,3}", cleaned[-1]) else cleaned[-1]


def anchor_candidates(finding: ReviewFinding) -> list[str]:
    anchors = [finding.anchor_text]
    if finding.anchor_text == "지필평가":
        anchors.append("지필 평가")
    if finding.anchor_text == "서술형":
        anchors.append("서술 형")
    point_percent = re.match(r"(\d+(?:\.\d+)?)점\s*\(\s*(\d+(?:\.\d+)?)%\s*\)", finding.anchor_text)
    if point_percent:
        point, percent = point_percent.groups()
        anchors.extend([
            f"{point}점({percent}%)",
            f"{point}점 ({percent}%)",
            f"{point}점  ({percent}%)",
            f"{point}점",
            f"{percent}%",
        ])
    is_timing_finding = "평가시기" in finding.topic or "평가시기" in finding.memo_text
    is_score_interval_finding = "배점 급간" in finding.topic
    if not is_timing_finding:
        codes = CODE_RE.findall(finding.memo_text)
        anchors.extend(codes)
    if is_timing_finding:
        anchors.extend(re.findall(r"\d+월\s*\d+(?:~\d+)?주", finding.context))
    if "배점" in finding.topic and not is_score_interval_finding and finding.anchor_text not in {"기본점수", "평가기준", "성취기준"}:
        anchors.extend(re.findall(r"(?<![0-9])(?:10|9|8|7|6|5|4|3|2)(?![0-9])", finding.context))
    # Deduplicate while preserving order.
    seen = set()
    result = []
    for anchor in anchors:
        if anchor and anchor not in seen:
            seen.add(anchor)
            result.append(anchor)
    return result


def dedupe_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    seen = set()
    result = []
    for finding in findings:
        key = (
            str(finding.file_path),
            finding.topic,
            finding.anchor_text,
            finding.memo_text,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
