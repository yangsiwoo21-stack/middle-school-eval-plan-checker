# 작업 인수인계

## 작업 기준

- 저장소: `yangsiwoo21-stack/middle-school-eval-plan-checker`
- 브랜치: `feature/desktop-eval-checker`
- 앱: `01_평가계획_점검기_소스/평가계획_점검기_최신_v0.3.4/assessment_checker_app/`

## 2026-07-18 작업 내용

- 2026학년도 2학기 1차 검토본 27개를 평가명, 영역 만점, 반영비율 중심으로 재검토함.
- 같은 평가요소에서 최고점보다 큰 점수가 뒤에 등장하는 명백한 배점 역전 검출 규칙을 추가함.
- 기본 양식의 참고용 유의사항, 수행평가 작성 안내문, 교과와 맞지 않는 성취도 선택표 검출 규칙을 추가함.
- 1학년 보건에서 `30-82-26` 배점 오기입과 4번-6번 수행평가 영역명 불일치를 확인함.
- 기존 메모를 보존하면서 범교과, 성취기준, 완료 처리, 최종 검수 메모를 관리하는 보조 도구를 추가함.
- 제출 평가계획 폴더가 GitHub에 올라가지 않도록 `.gitignore`를 보강함.

## 검증 결과

- `python smoke_test.py`: 통과
- `python -m py_compile checker.py finalize_score_template_memos.py`: 통과
- 검토본 HWPX 27개 ZIP 무결성: 정상
- 프로그램 진단 결과 대비 필요한 활성 메모 누락: 0건

## 다음 작업

- 학교 PC에서 작업을 시작하기 전에 `git pull origin feature/desktop-eval-checker` 실행.
- 실제 선생님 수정본이 도착하면 완료된 메모는 취소선 처리하고 남은 오류만 재검토.
- 1학년 보건 `82점`이 `28점` 오기입인지 작성 교사 확인 필요.

