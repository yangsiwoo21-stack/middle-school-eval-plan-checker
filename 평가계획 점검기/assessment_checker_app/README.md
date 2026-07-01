# 평가계획 점검 도우미

중학교 교수학습 및 평가 운영 계획 HWPX 파일을 로컬 PC에서 점검하는 데스크톱 프로그램입니다. 폴더 또는 단일 파일을 선택하면 평가계획 문서를 분석하고, 오류 후보를 화면 표와 JSON/CSV 결과로 정리하며, 필요한 경우 HWPX 복사본에 메모를 삽입합니다.

## 주요 기능

- HWPX 평가계획 파일 직접 읽기
- 학년, 과목, 학기 정보 추정
- 학년별 교육과정 기준 적용
  - 1, 2학년: 2022 개정 교육과정 기준
  - 3학년: 2015 개정 교육과정 기준
- 합본 HWPX 파일을 과목별 문서로 분리하여 점검
- 평가의 종류와 반영비율 표 분석
- 수행평가 영역, 기본점수, 평가요소 최하점 범위 점검
- 정기시험 1차/2차 성취기준 중복 의심 점검
- 수행평가 성취기준과 평가기준 코드 불일치 의심 점검
- 장기 미인정 결석자, 백지 제출자, 자발적 미참여자 점수 관련 점검
- HWPX 복사본에 오류 후보 메모 삽입
- JSON/CSV 결과 저장

## 실행 방법

Python이 설치되어 있다면 다음 명령으로 실행합니다.

```powershell
cd assessment_checker_app
python app.py
```

PowerShell 실행 스크립트를 사용할 수도 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File ".\run_app.ps1"
```

EXE로 패키징한 경우에는 생성된 `평가계획점검기.exe`를 실행하면 됩니다. 단, `dist/`, `build/` 등 패키징 산출물은 GitHub 저장소에 포함하지 않습니다.

## 파일 구조

```text
assessment_checker_app/
  app.py                       # Tkinter 기반 데스크톱 UI
  checker.py                   # HWPX 파싱 및 규칙 점검 로직
  run_app.ps1                  # PowerShell 실행 스크립트
  smoke_test.py                # 간단 실행 확인용 스크립트
  semester2_template_notes.json
  assets/
    pomeranian_icon.ico
    pomeranian_mascot.png
```

## 개발 확인

문법 검사는 다음 명령으로 실행할 수 있습니다.

```powershell
python -m py_compile app.py checker.py
```

간단한 동작 확인은 다음 명령으로 실행합니다.

```powershell
python smoke_test.py
```

## GitHub 포함 제외 대상

평가계획 원본 파일, 결과 파일, 학교 내부 자료, 개인정보가 포함될 수 있는 파일은 저장소에 올리지 않습니다.

제외 대상 예시는 다음과 같습니다.

- `*.hwpx`, `*.hwp`
- `*.pdf`
- `*.xlsx`, `*.xls`, `*.csv`
- `.env`
- `.streamlit/secrets.toml`
- `__pycache__/`
- `.venv/`, `venv/`
- `dist/`, `build/`
- `uploads/`, `outputs/`, `temp/`, `tmp/`
- 테스트용 평가계획 원본 파일
- 학교 내부 자료 또는 개인정보 포함 파일

## 학교 PC와 집 PC에서 작업하는 방법

이 프로젝트는 학교 PC와 집 PC 두 대에서 번갈아 개발할 수 있도록 GitHub를 기준 저장소로 사용합니다. 외장하드 대신 GitHub의 최신 버전을 기준으로 삼고, 작업 시작 전에는 항상 Pull, 작업 종료 후에는 Commit + Push를 진행합니다.

### 작업 시작 전 Pull

작업을 시작하기 전에 저장소 루트에서 아래 파일을 실행합니다.

```cmd
깃허브 업데이트.bat
```

직접 명령어로 실행할 경우:

```cmd
git status
git fetch
git pull
```

### 작업 종료 후 Commit + Push

작업이 끝나면 먼저 인수인계 파일을 최신 상태로 수정합니다.

```text
WORK_HANDOFF.md
```

그다음 저장소 루트에서 아래 파일을 실행합니다.

```cmd
깃허브 업로드.bat
```

직접 명령어로 실행할 경우:

```cmd
git status
git add .
git status
git commit -m "작업 내용"
git push
```

`WORK_HANDOFF.md`를 수정한 상태에서 `깃허브 업로드.bat`을 실행하면 인수인계 파일도 함께 GitHub에 올라갑니다. 반대로 `깃허브 업데이트.bat`은 GitHub의 최신 내용을 내려받는 파일이므로 업로드는 하지 않습니다.

### 평가계획 원본 파일은 GitHub에 올리지 않기

평가계획 원본과 생성 결과물에는 학교 내부 자료나 개인정보가 포함될 수 있으므로 GitHub에 올리지 않습니다.

올리면 안 되는 대표 파일:

- `.hwpx`
- `.hwp`
- `.pdf`
- `.xlsx`, `.xls`, `.csv`
- `.env`
- `secrets.toml`
- `assessment_checker_output/`
- `dist/`, `build/`
- `uploads/`, `outputs/`, `temp/`, `tmp/`

### OneDrive/Google Drive 안에 Git 저장소를 통째로 넣지 않기

Git 저장소 전체를 OneDrive나 Google Drive 동기화 폴더 안에 넣으면 동기화 충돌, 파일 잠금, 권한 문제가 생길 수 있습니다. 가능하면 Git 저장소는 일반 작업 폴더에 두고, 평가계획 원본 문서만 OneDrive/Google Drive에서 따로 관리합니다.

이미 OneDrive 안에서 작업 중이라면 다음 원칙을 지킵니다.

- 작업 시작 전 `update_from_github.bat` 실행
- 작업 중 OneDrive 동기화가 끝난 뒤 파일 열기
- 작업 종료 전 `WORK_HANDOFF.md` 업데이트
- 작업 종료 후 `upload_to_github.bat` 실행
