"""비개발자용 CuePrecise 첫 연결 화면."""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import configuration
import installer_support


API_KEY_URL = "https://aistudio.google.com/api-keys"


def install_directory() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


class OnboardingApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CuePrecise 시작하기")
        # 크기는 위젯을 다 만든 뒤 `_fit_to_contents` 가 내용에 맞춰 정한다.
        self.root.configure(padx=34, pady=28)

        style = ttk.Style()
        style.configure("Title.TLabel", font=("Malgun Gothic", 18, "bold"))
        style.configure("Step.TLabel", font=("Malgun Gothic", 11, "bold"))
        style.configure("Body.TLabel", font=("Malgun Gothic", 10))
        style.configure("Action.TButton", font=("Malgun Gothic", 11, "bold"), padding=(18, 10))

        ttk.Label(root, text="CuePrecise를 이 PC의 AI 앱에 연결합니다", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="유튜브 영상에서 찾던 그 대목을 정확히 짚어 줍니다. 어려운 설정은 CuePrecise가 자동으로 처리하니 Gemini API 키만 붙여넣어 주세요.",
            style="Body.TLabel", wraplength=540,
        ).pack(anchor="w", pady=(8, 24))

        ttk.Label(root, text="1. 무료 Gemini API 키 만들기", style="Step.TLabel").pack(anchor="w")
        link = tk.Label(root, text="Google AI Studio에서 API 키 열기 ↗", fg="#1769aa",
                        cursor="hand2", font=("Malgun Gothic", 10, "underline"))
        link.pack(anchor="w", pady=(6, 20))
        link.bind("<Button-1>", lambda _event: webbrowser.open(API_KEY_URL))

        ttk.Label(root, text="2. 복사한 키 붙여넣기", style="Step.TLabel").pack(anchor="w")
        key_row = ttk.Frame(root)
        key_row.pack(fill="x", pady=(8, 4))
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_var, show="●", font=("Consolas", 11))
        self.key_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(key_row, text="붙여넣기", command=self.paste_key).pack(side="left", padx=(8, 0))

        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="입력한 키 보기", variable=self.show_var,
                        command=self.toggle_key).pack(anchor="w")
        ttk.Label(root, text="키는 AI 앱이 CuePrecise를 사용할 수 있도록 이 PC의 앱 설정에만 저장됩니다.",
                  style="Body.TLabel", foreground="#666666", wraplength=540).pack(anchor="w", pady=(5, 20))

        ttk.Label(root, text="3. 연결할 앱 고르기", style="Step.TLabel").pack(anchor="w")
        self.client_vars = self._build_client_choices(root)

        ttk.Label(root, text="4. 연결하기", style="Step.TLabel").pack(anchor="w")
        self.action = ttk.Button(root, text="자동 설치하고 연결", style="Action.TButton",
                                 command=self.start_connect)
        self.action.pack(fill="x", pady=(10, 12))
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="FFmpeg 설치와 앱 설정을 자동으로 처리합니다.")
        self.status = ttk.Label(root, textvariable=self.status_var, style="Body.TLabel",
                                wraplength=540, justify="left")
        self.status.pack(anchor="w", pady=(10, 0))
        self._fit_to_contents()
        self.key_entry.focus_set()

    def _build_client_choices(self, root: tk.Misc) -> dict:
        """찾은 앱만 체크박스로 보여 준다.

        없는 앱까지 잠긴 줄로 늘어놓으면 목록이 앱 수만큼 길어진다. 창 아래의
        연결 버튼이 화면 밖으로 밀려 **연결 자체가 불가능해진다.** 실제로 그렇게
        됐다. 없는 앱은 한 줄로 이름만 알린다.
        """
        frame = ttk.Frame(root)
        frame.pack(fill="x", pady=(8, 18))
        chosen: dict = {}
        self.client_targets: dict = {}
        missing: list[str] = []
        for target in configuration.CLIENTS:
            try:
                installed = target.is_installed()
            except Exception:
                installed = False
            if not installed:
                missing.append(target.label)
                continue
            variable = tk.BooleanVar(value=True)
            ttk.Checkbutton(frame, text=target.label, variable=variable).pack(anchor="w")
            chosen[target.key] = variable
            self.client_targets[target.key] = target
        if not chosen:
            ttk.Label(frame, text="연결할 수 있는 AI 앱을 찾지 못했습니다. "
                                  "Claude Desktop, Codex, Claude Code, VS Code 중 하나를 "
                                  "설치한 뒤 다시 열어 주세요.",
                      style="Body.TLabel", foreground="#a33", wraplength=540).pack(anchor="w")
        elif missing:
            ttk.Label(frame, text="이 PC에서 찾지 못한 앱: " + ", ".join(missing),
                      style="Body.TLabel", foreground="#666666",
                      wraplength=540).pack(anchor="w", pady=(4, 0))
        return chosen

    def _fit_to_contents(self) -> None:
        """창을 내용에 맞춘다.

        크기를 숫자로 못 박아 두면 내용이 한 줄만 늘어도 아래가 잘린다. 잘리는
        것은 언제나 마지막에 놓인 것, 곧 연결 버튼이다.
        """
        self.root.update_idletasks()
        width = max(620, self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight()
        # 화면보다 큰 창은 그 자체로 잘린다. 작업 표시줄 몫도 남긴다.
        height = min(height, int(self.root.winfo_screenheight() * 0.88))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(560, width), min(460, height))

    def _selected_targets(self) -> list:
        """체크박스를 만들 때 쓴 그 앱들만 돌려준다.

        전역 목록을 다시 읽으면, 화면에 그린 것과 붙이는 것이 어긋날 수 있다.
        """
        return [target for key, target in self.client_targets.items()
                if self.client_vars[key].get()]

    def paste_key(self) -> None:
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            self.status_var.set("클립보드에 복사된 글자가 없습니다. API 키를 먼저 복사해 주세요.")
            return
        self.key_var.set(installer_support.normalize_api_key(value))
        self.status_var.set("키를 붙여넣었습니다. 아래 연결 버튼을 눌러 주세요.")

    def toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_var.get() else "●")

    def start_connect(self) -> None:
        key, error = installer_support.validate_api_key(self.key_var.get())
        if error:
            self.status_var.set(error)
            self.key_entry.focus_set()
            return
        targets = self._selected_targets()
        if not targets:
            self.status_var.set("연결할 앱을 하나 이상 골라 주세요.")
            return
        self.key_var.set(key)
        self.action.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("필요한 영상 도구를 확인하고 있습니다. 처음 설치하면 몇 분 걸릴 수 있습니다…")
        threading.Thread(target=self._connect_worker, args=(key, targets), daemon=True).start()

    def _connect_worker(self, key: str, targets: list) -> None:
        try:
            result = installer_support.connect_clients(key, install_directory(), targets=targets)
        except Exception as error:
            self.root.after(0, self._failed, str(error))
        else:
            self.root.after(0, self._succeeded, result)

    def _failed(self, detail: str) -> None:
        self.progress.stop()
        self.action.configure(state="normal")
        self.status_var.set(detail)
        messagebox.showerror("연결하지 못했습니다", detail + "\n\n설정과 기존 데이터는 삭제되지 않았습니다.")

    def _succeeded(self, result: dict) -> None:
        self.progress.stop()
        self.action.configure(text="연결 완료", state="disabled")
        names = "、".join(item["label"] for item in result["connected"])
        self.status_var.set(f"✓ {names}에 연결했습니다. 각 앱을 완전히 종료한 뒤 다시 실행하세요.")
        lines = [f"CuePrecise가 {names}에 연결되었습니다.", ""]
        # 일부만 실패했으면 성공만 알리고 끝내지 않는다. 무엇이 남았는지 말한다.
        if result["failed"]:
            lines.append("연결하지 못한 앱:")
            lines += [f"  · {item['label']}: {item['reason']}" for item in result["failed"]]
            lines.append("")
        lines += ["연결한 앱을 완전히 종료한 뒤 다시 실행하세요.",
                  "새 대화에서 ‘이 유튜브 영상을 분석해줘’라고 요청하면 됩니다."]
        messagebox.showinfo("설치 완료", "\n".join(lines))


def main() -> None:
    if sys.argv[1:] == ["--uninstall"]:
        # 붙였던 앱 전부에서 뗀다. 하나만 떼면 나머지에 없는 서버가 남는다.
        installer_support.disconnect_clients()
        return
    if sys.argv[1:] == ["--migrate"]:
        # 설치 프로그램이 조용히 부른다. 실패해도 설치를 막지 않는다.
        try:
            installer_support.migrate(install_directory())
        except Exception:
            pass
        return
    root = tk.Tk()
    OnboardingApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
