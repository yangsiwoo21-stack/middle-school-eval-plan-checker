# 작업 인수인계

학교 PC와 집 PC에서 번갈아 작업할 때, 작업 종료 전에 이 파일을 최신 상태로 갱신한 뒤 `upload_to_github.bat`으로 GitHub에 업로드합니다.

## 현재 작업 기준

- 기준 저장소: GitHub `yangsiwoo21-stack/middle-school-eval-plan-checker`
- 기본 작업 브랜치: `feature/desktop-eval-checker`
- 앱 위치: `평가계획 점검기/assessment_checker_app/`

## 작업 시작 전

1. `깃허브 업데이트.bat` 실행
2. `git status`로 변경 사항 확인
3. 프로그램 실행 후 필요한 작업 진행

## 작업 종료 전

1. 오늘 수정한 내용 요약을 아래에 작성
2. 남은 문제 또는 다음 할 일을 아래에 작성
3. `깃허브 업로드.bat` 실행

## GitHub 배치파일 사용법

### 깃허브 업데이트.bat

- 작업 시작 전에 실행합니다.
- 현재 로컬 폴더를 `_github_update_backups/` 아래에 먼저 백업합니다.
- `git fetch`, `git pull`을 실행해서 GitHub 최신 버전을 내려받습니다.
- 이 파일은 내려받기 전용입니다. `WORK_HANDOFF.md`를 GitHub에 올리지는 않습니다.

### 깃허브 업로드.bat

- 작업 종료 전에 실행합니다.
- 먼저 `WORK_HANDOFF.md`에 오늘 작업 내용과 다음 할 일을 적습니다.
- 민감 파일 경고를 확인하고 `YES`를 입력하면 진행합니다.
- 커밋 메시지를 직접 입력합니다.
- `git add .`, `git commit`, `git push`를 실행합니다.
- `WORK_HANDOFF.md`가 수정되어 있으면 다른 변경 파일과 함께 GitHub에 업로드됩니다.

### 다른 PC로 옮길 때

- 두 배치파일만 따로 실행하면 안 되고, Git 저장소 루트 폴더에 두어야 합니다.
- 저장소 루트에는 `.git`, `.gitignore`, `WORK_HANDOFF.md`, `깃허브 업데이트.bat`, `깃허브 업로드.bat`, `평가계획 점검기/`가 함께 있어야 합니다.

## 최근 작업 요약

- GitHub 최신본을 집 PC의 `평가계획 점검기` 폴더에 새로 세팅함.
- 기존 로컬 작업본은 별도 백업 폴더로 보존함.
- 시작/종료용 Git 배치파일 추가.
- 민감 파일이 GitHub에 올라가지 않도록 `.gitignore` 정리.

## 다음 할 일

- 실제 평가계획 파일 점검 시 메모 위치와 검출 결과를 계속 확인.
- 학교 PC에서 작업 시작 전 반드시 `update_from_github.bat` 실행.
