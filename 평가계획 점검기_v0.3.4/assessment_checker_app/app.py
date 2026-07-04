from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from checker import ReviewFinding, ReviewRunner, StageReviewResult
from excel_review import ExcelReviewBuilder


APP_TITLE = "평가계획 점검 도우미 v0.3.4 배포용"
SIGNATURE_TEXT = "송우중 교사-양시우"


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


class AssessmentCheckerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x760")
        self.minsize(1040, 620)
        self.configure(bg="#f6fbff")

        self.project_dir = Path(__file__).resolve().parents[1]
        self.workspace_dir = self.project_dir.parent
        self.runner = ReviewRunner()
        self.excel_builder = ExcelReviewBuilder()
        self.findings: list[ReviewFinding] = []
        self.stage_results: list[StageReviewResult] = []
        self.last_review_mode = ""
        self.last_review_path: Path | None = None
        self.last_excel_path: Path | None = None
        self.last_memo_dir: Path | None = None
        self.pending_output_tasks = 0
        self.output_folder_opened = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.active_subject = "전체"
        self.subject_notebook: ttk.Notebook | None = None

        self.input_file = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value="")
        self.input_file_display = tk.StringVar(value="파일을 선택하세요")
        self.output_dir_display = tk.StringVar(value="파일이 있는 폴더에 자동 저장")
        self.status = tk.StringVar(value="파일을 선택한 뒤 파일 점검을 실행하세요.")
        self.mascot_image: tk.PhotoImage | None = None

        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#f6fbff")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat")
        style.configure("Header.TFrame", background="#eaf7f5")
        style.configure("App.TLabel", background="#f6fbff", foreground="#1f2d3d", font=("Malgun Gothic", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1f2d3d", font=("Malgun Gothic", 10))
        style.configure("Title.TLabel", background="#eaf7f5", foreground="#183b56", font=("Malgun Gothic", 17, "bold"))
        style.configure("Subtitle.TLabel", background="#eaf7f5", foreground="#486581", font=("Malgun Gothic", 10))
        style.configure("Signature.TLabel", background="#f6fbff", foreground="#66788a", font=("Malgun Gothic", 9))
        style.configure("Primary.TButton", font=("Malgun Gothic", 10, "bold"), padding=(14, 8))
        style.configure("App.TButton", font=("Malgun Gothic", 10), padding=(12, 7))
        style.configure("Treeview", font=("Malgun Gothic", 9), rowheight=28, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Malgun Gothic", 9, "bold"), background="#dff3ef", foreground="#183b56")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, padding=(10, 6), style="Header.TFrame")
        header.pack(fill=tk.X, pady=(0, 6))

        mascot_path = resource_path("assets/pomeranian_mascot.png")
        if mascot_path.exists():
            self.mascot_image = tk.PhotoImage(file=str(mascot_path)).subsample(4, 4)
            ttk.Label(header, image=self.mascot_image, background="#eaf7f5").pack(side=tk.LEFT, padx=(0, 10))

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(title_box, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="파일 선택 → 파일 점검 → 메모본·엑셀 검토표 자동 저장",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        top = ttk.Frame(root, padding=(12, 8), style="Panel.TFrame")
        top.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(top, text="평가계획 파일", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.input_file_display, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="파일 선택", command=self._choose_file, style="App.TButton").grid(row=0, column=2)

        top.columnconfigure(1, weight=1)

        controls = ttk.Frame(root, style="App.TFrame")
        controls.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(controls, text="파일 점검", command=self._run_file_review, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(controls, text="해당 폴더 열기", command=self._open_output_folder, style="App.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="해당 파일 열기", command=self._open_selected_source_file, style="App.TButton").pack(side=tk.LEFT)
        ttk.Button(controls, text="메모본 열기", command=self._open_selected_memo_file, style="Primary.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Label(controls, textvariable=self.status, style="App.TLabel").pack(side=tk.LEFT, padx=16)

        self.subject_notebook = ttk.Notebook(root)
        self.subject_notebook.pack(fill=tk.X, pady=(0, 4))
        self.subject_notebook.bind("<<NotebookTabChanged>>", self._on_subject_tab_changed)
        self._rebuild_subject_tabs()

        content = ttk.Frame(root, style="App.TFrame")
        content.pack(fill=tk.BOTH, expand=True)

        stage_panel = ttk.Frame(content, style="App.TFrame")
        stage_panel.pack(fill=tk.BOTH, expand=True)
        stage_label = ttk.Label(stage_panel, text="단계별 점검 결과", style="Panel.TLabel")
        stage_label.pack(anchor="w", pady=(0, 3))

        stage_columns = ("grade", "subject", "stage", "status", "message")
        self.stage_tree = ttk.Treeview(stage_panel, columns=stage_columns, show="headings", height=15)
        stage_headers = {
            "grade": "학년",
            "subject": "과목",
            "stage": "점검 단계",
            "status": "상태",
            "message": "점검 내용",
        }
        stage_widths = {
            "grade": 56,
            "subject": 86,
            "stage": 210,
            "status": 90,
            "message": 780,
        }
        for column in stage_columns:
            self.stage_tree.heading(column, text=stage_headers[column])
            self.stage_tree.column(column, width=stage_widths[column], anchor="w")
        self.stage_tree.pack(fill=tk.BOTH, expand=True)

        stage_scroll = ttk.Scrollbar(self.stage_tree, orient="vertical", command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stage_scroll.set)
        stage_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        findings_panel = ttk.Frame(content, style="App.TFrame")
        findings_panel.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        findings_label = ttk.Label(findings_panel, text="수정 권고 및 메모 대상", style="Panel.TLabel")
        findings_label.pack(anchor="w", pady=(0, 3))

        columns = ("grade", "subject", "topic", "message")
        self.tree = ttk.Treeview(findings_panel, columns=columns, show="headings", selectmode="extended")
        headers = {
            "grade": "학년",
            "subject": "과목",
            "topic": "오류 주제",
            "message": "수정 권고",
        }
        widths = {
            "grade": 56,
            "subject": 86,
            "topic": 220,
            "message": 820,
        }
        for column in columns:
            self.tree.heading(column, text=headers[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(self.tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(root, style="App.TFrame")
        bottom.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            bottom,
            text="점검이 끝나면 HWPX 메모본과 엑셀 검토표가 자동 생성됩니다. 결과를 선택한 뒤 원본 파일 또는 메모본을 바로 열 수 있습니다.",
            style="App.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(bottom, text=SIGNATURE_TEXT, style="Signature.TLabel").pack(side=tk.RIGHT)

    def _choose_file(self) -> None:
        current = Path(self.input_file.get())
        initial_dir = str(current.parent) if current.exists() else str(self.workspace_dir)
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            title="평가계획 파일 선택",
            filetypes=[
                ("평가계획 파일", "*.hwpx *.hwp *.md"),
                ("HWPX 파일", "*.hwpx"),
                ("HWP 파일", "*.hwp"),
                ("Markdown 파일", "*.md"),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.input_file.set(path)
            self.input_file_display.set(self._short_path(path))
            self._set_output_dir(Path(path).parent)

    def _set_output_dir(self, path: Path) -> None:
        self.output_dir.set(str(path))
        self.output_dir_display.set(self._short_path(path))

    def _short_path(self, value: str | Path) -> str:
        if not value:
            return ""
        path = Path(value)
        if path.name:
            parent = path.parent.name
            return f"...\\{parent}\\{path.name}" if parent else path.name
        return str(path)

    def _run_file_review(self) -> None:
        path = Path(self.input_file.get())
        if not path.exists():
            messagebox.showerror(APP_TITLE, "평가계획 파일을 선택해 주세요.")
            return
        if path.suffix.lower() == ".hwp":
            messagebox.showwarning(APP_TITLE, "현재 자동 점검은 HWPX/MD 파일을 우선 지원합니다. HWP는 HWPX로 변환한 뒤 점검해 주세요.")
            return
        self._set_output_dir(path.parent)
        self._start_review("file", path)

    def _start_review(self, mode: str, path: Path) -> None:
        self.status.set("점검 중...")
        self.last_review_mode = mode
        self.last_review_path = path
        self.stage_results = []
        self.stage_tree.delete(*self.stage_tree.get_children())
        self.tree.delete(*self.tree.get_children())
        thread = threading.Thread(target=self._run_review_worker, args=(mode, path), daemon=True)
        thread.start()

    def _run_review_worker(self, mode: str, path: Path) -> None:
        try:
            if mode == "file":
                findings, stages = self.runner.review_file_with_stages(path)
            else:
                findings, stages = self.runner.review_folder_with_stages(path)
            self.events.put(("review_done", {"findings": findings, "stages": stages}))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "review_done":
                    result = dict(payload)  # type: ignore[arg-type]
                    self.findings = list(result.get("findings", []))
                    self.stage_results = list(result.get("stages", []))
                    self._rebuild_subject_tabs()
                    self._render_stage_results()
                    self._render_findings()
                    self.output_folder_opened = False
                    self.pending_output_tasks = 1 + (1 if self.findings else 0)
                    if self.findings:
                        self.status.set(f"점검 완료: 오류 후보 {len(self.findings)}건 / 메모본·엑셀 검토표 생성 중...")
                        self._start_memo_creation(self.findings)
                    else:
                        self.status.set("점검 완료: 오류 후보 없음 / 엑셀 검토표 생성 중...")
                    self._save_excel_review(silent=True)
                elif event == "memo_done":
                    self.last_memo_dir = Path(str(payload))
                    self.status.set(f"메모본 생성 완료: {payload}")
                    self._mark_output_task_done()
                elif event == "excel_done":
                    self.last_excel_path = Path(str(payload))
                    self.status.set(f"엑셀 검토표 생성 완료: {payload}")
                    self._mark_output_task_done()
                elif event == "error":
                    self.status.set("오류 발생")
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _mark_output_task_done(self) -> None:
        self.pending_output_tasks = max(0, self.pending_output_tasks - 1)
        if self.pending_output_tasks == 0 and not self.output_folder_opened:
            self.output_folder_opened = True
            self._open_output_folder(silent=True)

    def _render_findings(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, finding in enumerate(self.findings):
            if not self._matches_active_subject(finding.subject):
                continue
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    finding.grade or "",
                    finding.subject or "",
                    finding.topic,
                    finding.memo_text.replace("\n", " / "),
                ),
            )

    def _render_stage_results(self) -> None:
        self.stage_tree.delete(*self.stage_tree.get_children())
        for index, result in enumerate(self.stage_results):
            if not self._matches_active_subject(result.subject):
                continue
            self.stage_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    result.grade or "",
                    result.subject or "",
                    f"{result.stage_no}단계 {result.stage_name}" if result.stage_no != 99 else result.stage_name,
                    result.status,
                    result.message,
                ),
            )

    def _rebuild_subject_tabs(self) -> None:
        if self.subject_notebook is None:
            return
        subjects = self._available_subjects()
        if self.active_subject not in subjects:
            self.active_subject = "전체"

        self.subject_notebook.unbind("<<NotebookTabChanged>>")
        for tab_id in self.subject_notebook.tabs():
            self.subject_notebook.forget(tab_id)
        for subject in subjects:
            frame = ttk.Frame(self.subject_notebook, style="App.TFrame")
            self.subject_notebook.add(frame, text=subject)
        try:
            self.subject_notebook.select(subjects.index(self.active_subject))
        except tk.TclError:
            pass
        self.subject_notebook.bind("<<NotebookTabChanged>>", self._on_subject_tab_changed)

    def _available_subjects(self) -> list[str]:
        subjects = {
            str(subject)
            for subject in [*(finding.subject for finding in self.findings), *(stage.subject for stage in self.stage_results)]
            if subject
        }
        ordered = sorted(subjects, key=self._subject_sort_key)
        return ["전체", *ordered]

    def _subject_sort_key(self, subject: str) -> tuple[int, str]:
        order = [
            "국어",
            "도덕",
            "사회",
            "역사",
            "수학",
            "과학",
            "기술가정",
            "체육",
            "음악",
            "미술",
            "영어",
        ]
        return (order.index(subject) if subject in order else 99, subject)

    def _on_subject_tab_changed(self, _event: object | None = None) -> None:
        if self.subject_notebook is None:
            return
        selected = self.subject_notebook.select()
        if not selected:
            return
        self.active_subject = self.subject_notebook.tab(selected, "text")
        self._render_stage_results()
        self._render_findings()

    def _matches_active_subject(self, subject: str | None) -> bool:
        return self.active_subject == "전체" or subject == self.active_subject

    def _selected_findings(self) -> list[ReviewFinding]:
        selected = self.tree.selection()
        if not selected:
            return self.findings
        return [self.findings[int(iid)] for iid in selected]

    def _first_selected_finding(self) -> ReviewFinding | None:
        selected = self.tree.selection()
        if selected:
            return self.findings[int(selected[0])]
        if self.findings:
            return self.findings[0]
        return None

    def _open_selected_source_file(self) -> None:
        finding = self._first_selected_finding()
        if not finding:
            selected = Path(self.input_file.get())
            if selected.exists():
                self._open_file(selected)
                return
            messagebox.showwarning(APP_TITLE, "열 파일이 없습니다. 먼저 평가계획 파일을 선택해 주세요.")
            return
        self._open_file(finding.file_path)

    def _open_output_folder(self, silent: bool = False) -> None:
        out_dir = Path(self.output_dir.get())
        if not out_dir.exists():
            if not silent:
                messagebox.showwarning(APP_TITLE, "출력 폴더를 찾을 수 없습니다.")
            return
        self._open_file(out_dir)

    def _open_selected_memo_file(self) -> None:
        finding = self._first_selected_finding()
        if not finding:
            messagebox.showwarning(APP_TITLE, "열 메모본이 없습니다. 먼저 점검 결과를 선택해 주세요.")
            return
        memo_path = self._memo_copy_path_for(finding.file_path)
        if not memo_path:
            messagebox.showwarning(APP_TITLE, "메모본을 찾지 못했습니다. 먼저 HWPX 메모본 생성을 실행해 주세요.")
            return
        self._open_file(memo_path)

    def _memo_copy_path_for(self, source_path: Path) -> Path | None:
        memo_dir = Path(self.output_dir.get())
        summary_path = memo_dir / "assessment_memo_creation_summary.json"
        if summary_path.exists():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                for item in data:
                    if Path(item.get("source", "")) == source_path:
                        output = Path(item.get("output", ""))
                        if output.exists():
                            return output
            except Exception:
                pass
        candidates = sorted(source_path.parent.glob(f"{source_path.stem}_메모첨부*.hwpx"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def _open_file(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror(APP_TITLE, f"파일을 찾을 수 없습니다.\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"파일을 열 수 없습니다.\n{exc}")

    def _save_results(self) -> None:
        if not self.findings and not self.stage_results:
            messagebox.showwarning(APP_TITLE, "저장할 점검 결과가 없습니다.")
            return
        out_dir = Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "assessment_findings.json"
        csv_path = out_dir / "assessment_findings.csv"
        stage_json_path = out_dir / "assessment_stage_results.json"
        data = [finding.to_dict() for finding in self.findings]
        stage_data = [result.to_dict() for result in self.stage_results]
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        stage_json_path.write_text(json.dumps(stage_data, ensure_ascii=False, indent=2), encoding="utf-8")
        if data:
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)
            messagebox.showinfo(APP_TITLE, f"결과 저장 완료\n{json_path}\n{csv_path}\n{stage_json_path}")
        else:
            messagebox.showinfo(APP_TITLE, f"결과 저장 완료\n{json_path}\n{stage_json_path}")

    def _save_excel_review(self, silent: bool = False) -> None:
        if not self.last_review_path:
            if not silent:
                messagebox.showwarning(APP_TITLE, "먼저 파일 점검을 실행해 주세요.")
            return
        out_dir = Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = self.last_review_path.stem if self.last_review_path.is_file() else self.last_review_path.name
        output_path = out_dir / f"{stem}_v0.3.4_엑셀검토표.xlsx"
        if not silent:
            self.status.set("엑셀 검토표 생성 중...")
        thread = threading.Thread(target=self._save_excel_worker, args=(self.last_review_path, output_path), daemon=True)
        thread.start()

    def _save_excel_worker(self, target: Path, output_path: Path) -> None:
        try:
            self.excel_builder.build([target], self.findings, output_path)
            self.events.put(("excel_done", str(output_path)))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _create_memo_copies(self) -> None:
        findings = self.findings
        if not findings:
            messagebox.showwarning(APP_TITLE, "메모로 넣을 오류 후보가 없습니다.")
            return
        self._start_memo_creation(findings)

    def _start_memo_creation(self, findings: list[ReviewFinding]) -> None:
        out_dir = Path(self.output_dir.get())
        self.status.set("HWPX 메모본 생성 중...")
        thread = threading.Thread(target=self._create_memo_worker, args=(findings, out_dir), daemon=True)
        thread.start()

    def _create_memo_worker(self, findings: list[ReviewFinding], out_dir: Path) -> None:
        try:
            self.runner.create_memo_copies(findings, out_dir)
            self.events.put(("memo_done", str(out_dir)))
        except Exception as exc:
            self.events.put(("error", str(exc)))


if __name__ == "__main__":
    app = AssessmentCheckerApp()
    app.mainloop()
