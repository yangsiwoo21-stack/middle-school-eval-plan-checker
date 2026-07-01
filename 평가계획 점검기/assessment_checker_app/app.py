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

from checker import ReviewFinding, ReviewRunner


APP_TITLE = "평가계획 점검 도우미"
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
        self.findings: list[ReviewFinding] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.input_dir = tk.StringVar(value=str(self.workspace_dir / "1학기 평가계획 샘플"))
        self.input_file = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value=str(self.project_dir / "assessment_checker_output"))
        self.input_dir_display = tk.StringVar(value=self._short_path(self.input_dir.get()))
        self.input_file_display = tk.StringVar(value="파일을 선택하세요")
        self.output_dir_display = tk.StringVar(value="평가계획 폴더/파일 위치에 자동 저장")
        self.output_manually_selected = False
        self.status = tk.StringVar(value="폴더 또는 파일을 선택한 뒤 점검을 실행하세요.")
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
        style.configure("Title.TLabel", background="#eaf7f5", foreground="#183b56", font=("Malgun Gothic", 20, "bold"))
        style.configure("Subtitle.TLabel", background="#eaf7f5", foreground="#486581", font=("Malgun Gothic", 10))
        style.configure("Signature.TLabel", background="#f6fbff", foreground="#66788a", font=("Malgun Gothic", 9))
        style.configure("Primary.TButton", font=("Malgun Gothic", 10, "bold"), padding=(14, 8))
        style.configure("App.TButton", font=("Malgun Gothic", 10), padding=(12, 7))
        style.configure("Treeview", font=("Malgun Gothic", 9), rowheight=28, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Malgun Gothic", 9, "bold"), background="#dff3ef", foreground="#183b56")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14, style="App.TFrame")
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, padding=16, style="Header.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))

        mascot_path = resource_path("assets/pomeranian_mascot.png")
        if mascot_path.exists():
            self.mascot_image = tk.PhotoImage(file=str(mascot_path))
            ttk.Label(header, image=self.mascot_image, background="#eaf7f5").pack(side=tk.LEFT, padx=(0, 14))

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(title_box, text="평가계획 점검 도우미", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="HWPX 평가계획을 읽고 오류 후보를 찾은 뒤, 근거 위치에 메모본을 자동 생성합니다.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(
            title_box,
            text="추천 흐름: 폴더/파일 선택 → 점검 실행 → 결과 확인 → 메모본 열기",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        top = ttk.Frame(root, padding=14, style="Panel.TFrame")
        top.pack(fill=tk.X)

        ttk.Label(top, text="평가계획 폴더", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.input_dir_display, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="폴더 선택", command=self._choose_input, style="App.TButton").grid(row=0, column=2)

        ttk.Label(top, text="평가계획 파일", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.input_file_display, state="readonly").grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(top, text="파일 선택", command=self._choose_file, style="App.TButton").grid(row=1, column=2, pady=(8, 0))

        ttk.Label(top, text="출력 폴더", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.output_dir_display, state="readonly").grid(row=2, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(top, text="출력 선택", command=self._choose_output, style="App.TButton").grid(row=2, column=2, pady=(8, 0))
        top.columnconfigure(1, weight=1)

        controls = ttk.Frame(root, style="App.TFrame")
        controls.pack(fill=tk.X, pady=10)
        ttk.Button(controls, text="폴더 점검", command=self._run_folder_review, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(controls, text="파일 점검", command=self._run_file_review, style="Primary.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="결과 저장", command=self._save_results, style="App.TButton").pack(side=tk.LEFT)
        ttk.Button(controls, text="HWPX 메모본 생성", command=self._create_memo_copies, style="App.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="해당 파일 열기", command=self._open_selected_source_file, style="App.TButton").pack(side=tk.LEFT)
        ttk.Button(controls, text="메모본 열기", command=self._open_selected_memo_file, style="App.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Label(controls, textvariable=self.status, style="App.TLabel").pack(side=tk.LEFT, padx=16)

        columns = ("grade", "subject", "topic", "message")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="extended")
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
            text="점검이 끝나면 오류 후보 전체를 기준으로 HWPX 메모본이 자동 생성됩니다. 결과를 선택한 뒤 원본 파일 또는 메모본을 바로 열 수 있습니다.",
            style="App.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(bottom, text=SIGNATURE_TEXT, style="Signature.TLabel").pack(side=tk.RIGHT)

    def _choose_input(self) -> None:
        path = filedialog.askdirectory(initialdir=self.input_dir.get() or str(self.project_dir))
        if path:
            self.input_dir.set(path)
            self.input_dir_display.set(self._short_path(path))
            if not self.output_manually_selected:
                self._set_output_dir(Path(path))

    def _choose_file(self) -> None:
        initial_dir = self.input_dir.get() if Path(self.input_dir.get()).exists() else str(self.project_dir)
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
            if not self.output_manually_selected:
                self._set_output_dir(Path(path).parent)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_dir.get() or str(self.project_dir))
        if path:
            self.output_dir.set(path)
            self.output_dir_display.set(self._short_path(path))
            self.output_manually_selected = True

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

    def _run_folder_review(self) -> None:
        folder = Path(self.input_dir.get())
        if not folder.exists():
            messagebox.showerror(APP_TITLE, "평가계획 폴더를 찾을 수 없습니다.")
            return
        if not self.output_manually_selected:
            self._set_output_dir(folder)
        self._start_review("folder", folder)

    def _run_file_review(self) -> None:
        path = Path(self.input_file.get())
        if not path.exists():
            messagebox.showerror(APP_TITLE, "평가계획 파일을 선택해 주세요.")
            return
        if path.suffix.lower() == ".hwp":
            messagebox.showwarning(APP_TITLE, "현재 자동 점검은 HWPX/MD 파일을 우선 지원합니다. HWP는 HWPX로 변환한 뒤 점검해 주세요.")
            return
        if not self.output_manually_selected:
            self._set_output_dir(path.parent)
        self._start_review("file", path)

    def _start_review(self, mode: str, path: Path) -> None:
        self.status.set("점검 중...")
        self.tree.delete(*self.tree.get_children())
        thread = threading.Thread(target=self._run_review_worker, args=(mode, path), daemon=True)
        thread.start()

    def _run_review_worker(self, mode: str, path: Path) -> None:
        try:
            if mode == "file":
                findings = self.runner.review_file(path)
            else:
                findings = self.runner.review_folder(path)
            self.events.put(("review_done", findings))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "review_done":
                    self.findings = list(payload)  # type: ignore[arg-type]
                    self._render_findings()
                    if self.findings:
                        self.status.set(f"점검 완료: 오류 후보 {len(self.findings)}건 / 메모본 생성 중...")
                        self._start_memo_creation(self.findings)
                    else:
                        self.status.set("점검 완료: 오류 후보 없음")
                elif event == "memo_done":
                    self.status.set(f"메모본 생성 완료: {payload}")
                elif event == "error":
                    self.status.set("오류 발생")
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _render_findings(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, finding in enumerate(self.findings):
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
            messagebox.showwarning(APP_TITLE, "열 파일이 없습니다. 먼저 점검 결과를 선택해 주세요.")
            return
        self._open_file(finding.file_path)

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
        memo_dir = Path(self.output_dir.get()) / "memo_copies"
        summary_path = memo_dir / "memo_creation_summary.json"
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
        candidates = sorted(memo_dir.glob(f"{source_path.stem}_메모첨부*.hwpx"), key=lambda p: p.stat().st_mtime, reverse=True)
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
        if not self.findings:
            messagebox.showwarning(APP_TITLE, "저장할 점검 결과가 없습니다.")
            return
        out_dir = Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "assessment_findings.json"
        csv_path = out_dir / "assessment_findings.csv"
        data = [finding.to_dict() for finding in self.findings]
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0].keys()))
            writer.writeheader()
            writer.writerows(data)
        messagebox.showinfo(APP_TITLE, f"결과 저장 완료\n{json_path}\n{csv_path}")

    def _create_memo_copies(self) -> None:
        findings = self.findings
        if not findings:
            messagebox.showwarning(APP_TITLE, "메모로 넣을 오류 후보가 없습니다.")
            return
        self._start_memo_creation(findings)

    def _start_memo_creation(self, findings: list[ReviewFinding]) -> None:
        out_dir = Path(self.output_dir.get()) / "memo_copies"
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
