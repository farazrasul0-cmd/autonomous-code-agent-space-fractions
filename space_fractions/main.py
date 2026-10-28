from __future__ import annotations
import re
import tkinter as tk
from tkinter import ttk
from typing import Optional

from space_fractions.game_engine import GameEngine, GameState, TurnResult
from space_fractions.question_bank import Question
from space_fractions.ui_screens import FractionVisualizer, HUDFrame

class SpaceFractionsApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Space Fractions — Cosmic Fraction Explorer")
        self.root.geometry("840x620")
        self.root.configure(bg="#0B0F19")
        self.engine: GameEngine = GameEngine()
        self.current_question: Optional[Question] = None
        self.selected_level: int = 1
        self.feedback_active: bool = False
        self._setup_styles()
        self.main_container = ttk.Frame(self.root, padding="14")
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self._bind_shortcuts()
        self.show_title_screen()

    def _setup_styles(self) -> None:
        self.style = ttk.Style()
        try: self.style.theme_use("clam")
        except Exception: pass
        self.style.configure(".", background="#0B0F19", foreground="#F8FAFC")
        self.style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#06B6D4")
        self.style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#94A3B8")
        self.style.configure("Prompt.TLabel", font=("Segoe UI", 13, "bold"), foreground="#F8FAFC")
        self.style.configure("Card.TFrame", background="#111827")
        self.style.configure("Option.TButton", font=("Segoe UI", 11, "bold"), padding="8")
        self.style.configure("Action.TButton", font=("Segoe UI", 11, "bold"), padding="10")

    def _clear(self) -> None:
        for w in self.main_container.winfo_children(): w.destroy()

    def _bind_shortcuts(self) -> None:
        for i in range(4): self.root.bind(f"<Key-{i+1}>", lambda e, idx=i: self._on_key(idx))
        self.root.bind("<Key-p>", lambda e: self.toggle_pause())
        self.root.bind("<Key-P>", lambda e: self.toggle_pause())
        self.root.bind("<Return>", lambda e: self._on_enter())
        self.root.bind("<space>", lambda e: self._on_enter())

    def _on_key(self, idx: int) -> None:
        if self.engine and self.engine.state == GameState.PLAYING and not self.feedback_active:
            self.handle_answer(idx)

    def _on_enter(self) -> None:
        if hasattr(self, "btn_advance") and self.btn_advance.winfo_exists(): self.btn_advance.invoke()
        elif hasattr(self, "btn_start") and self.btn_start.winfo_exists(): self.btn_start.invoke()

    def show_title_screen(self) -> None:
        self._clear(); self.feedback_active = False
        h = ttk.Frame(self.main_container); h.pack(fill=tk.X, pady=(20, 10))
        ttk.Label(h, text="🚀  SPACE FRACTIONS", style="Title.TLabel").pack()
        ttk.Label(h, text="Grade 6 Cosmic Mathematics Learning Expedition", style="Subtitle.TLabel").pack(pady=(4, 0))
        box = ttk.Frame(self.main_container, padding="16", style="Card.TFrame"); box.pack(fill=tk.BOTH, expand=True, pady=16)
        ttk.Label(box, text="Select Mission Difficulty Level:", font=("Segoe UI", 12, "bold"), foreground="#38BDF8").pack(anchor=tk.W, pady=(0, 12))
        self.lvl_var = tk.IntVar(value=self.selected_level)
        for lvl, title, desc in [(1, "Level 1 — Cadet", "Simplification & Equivalent Fractions"), (2, "Level 2 — Pilot", "Common Denominators & Comparisons"), (3, "Level 3 — Commander", "Unlike Denominators & Mixed Arithmetic")]:
            rf = ttk.Frame(box, padding="4"); rf.pack(fill=tk.X, pady=2)
            ttk.Radiobutton(rf, text=f"{title}: {desc}", value=lvl, variable=self.lvl_var, command=lambda: setattr(self, "selected_level", self.lvl_var.get())).pack(side=tk.LEFT)
        ttk.Label(box, text="\nDirectives:\n• Solve fraction puzzles to navigate anomalies.\n• Correct solutions: +100 energy + streak bonuses.\n• Wrong solutions: -1 shield. Depleting 3 shields ends mission.\n• Controls: [1, 2, 3, 4] keys. [P] to pause.", font=("Segoe UI", 9), foreground="#94A3B8").pack(anchor=tk.W, pady=(14, 0))
        self.btn_start = ttk.Button(self.main_container, text="▶  Launch Mission", style="Action.TButton", command=self.start_mission)
        self.btn_start.pack(pady=10)

    def start_mission(self) -> None:
        self.engine = GameEngine(level=self.selected_level, total_questions=10)
        self.engine.play()
        self.show_gameplay_screen()

    def show_gameplay_screen(self) -> None:
        self._clear(); self.feedback_active = False
        if not self.engine or not self.engine.current_question:
            self.show_game_over_screen(); return
        self.current_question = self.engine.current_question
        self.hud = HUDFrame(self.main_container); self.hud.pack(fill=tk.X, pady=(0, 10))
        self.hud.update_hud(self.engine.shields, self.engine.score, self.engine.streak, self.engine.current_question_index, self.engine.total_questions, self.engine.level)
        st = ttk.Frame(self.main_container, padding="10", style="Card.TFrame"); st.pack(fill=tk.BOTH, expand=True, pady=6)
        ttk.Label(st, text=f"Mission Challenge {self.engine.current_question_index} — {self.current_question.topic}", font=("Segoe UI", 10, "bold"), foreground="#38BDF8").pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(st, text=self.current_question.prompt, style="Prompt.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.visualizer = FractionVisualizer(st, width=220, height=150); self.visualizer.pack(pady=6)
        n, d = self._extract_display_fraction(self.current_question.prompt)
        self.visualizer.draw_fraction(n, d, title="Sector Ratio")
        of = ttk.Frame(st); of.pack(fill=tk.X, pady=(8, 4))
        of.columnconfigure(0, weight=1); of.columnconfigure(1, weight=1)
        self.option_buttons = []
        for i, opt in enumerate(self.current_question.options):
            b = ttk.Button(of, text=f"[{i+1}]  {opt}", style="Option.TButton", command=lambda idx=i: self.handle_answer(idx))
            b.grid(row=i//2, column=i%2, padx=6, pady=6, sticky=tk.EW)
            self.option_buttons.append(b)
        ff = ttk.Frame(self.main_container); ff.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(ff, text="⏸  Pause Mission (P)", command=self.toggle_pause).pack(side=tk.RIGHT)

    def _extract_display_fraction(self, text: str) -> tuple[int, int]:
        m = re.findall(r"(\d+)/(\d+)", text)
        if m: return int(m[0][0]), int(m[0][1])
        if self.current_question:
            for opt in self.current_question.options:
                mo = re.findall(r"(\d+)/(\d+)", opt)
                if mo: return int(mo[0][0]), int(mo[0][1])
        return 1, 2

    def handle_answer(self, idx: int) -> None:
        if not self.engine or self.feedback_active: return
        self.feedback_active = True
        for b in self.option_buttons: b.configure(state=tk.DISABLED)
        res = self.engine.submit_answer(idx)
        self.show_feedback_screen(res)

    def show_feedback_screen(self, res: TurnResult) -> None:
        self._clear()
        box = ttk.Frame(self.main_container, padding="20", style="Card.TFrame"); box.pack(fill=tk.BOTH, expand=True, pady=16)
        st_text = "✨  MISSION TARGET RESOLVED! (CORRECT)" if res.is_correct else "⚠️  CALCULATION DISCREPANCY! (INCORRECT)"
        st_color = "#10B981" if res.is_correct else "#EF4444"
        rew_text = f"+{res.energy_awarded} Energy Units" if res.is_correct else "-1 Shield Integrity Lost"
        ttk.Label(box, text=st_text, font=("Segoe UI", 16, "bold"), foreground=st_color).pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(box, text=rew_text, font=("Segoe UI", 11, "bold"), foreground="#F8FAFC").pack(anchor=tk.W, pady=(0, 14))
        ef = ttk.LabelFrame(box, text=" Mathematical Analysis & Step-by-Step Explanation ", padding="12"); ef.pack(fill=tk.BOTH, expand=True, pady=8)
        ttk.Label(ef, text=res.explanation, font=("Segoe UI", 11), foreground="#E2E8F0", wraplength=700).pack(anchor=tk.W)
        ttk.Label(box, text=f"Energy: {res.current_score} | Shields: {res.current_shields} | Streak: {res.current_streak}", font=("Segoe UI", 10, "bold"), foreground="#94A3B8").pack(anchor=tk.W, pady=(10, 14))
        btn_text = "Advance to Mission Debrief ▶" if res.is_game_over else "Next Challenge ▶  (Enter)"
        self.btn_advance = ttk.Button(box, text=btn_text, style="Action.TButton", command=self.advance_after_feedback)
        self.btn_advance.pack(anchor=tk.E)

    def advance_after_feedback(self) -> None:
        self.feedback_active = False
        if not self.engine: self.show_title_screen(); return
        if self.engine.state == GameState.GAMEOVER: self.show_game_over_screen()
        else: self.engine.next_question(); self.show_gameplay_screen()

    def toggle_pause(self) -> None:
        if not self.engine: return
        if self.engine.state == GameState.PLAYING:
            self.engine.pause(); self._clear()
            pb = ttk.Frame(self.main_container, padding="24", style="Card.TFrame"); pb.pack(fill=tk.BOTH, expand=True, pady=30)
            ttk.Label(pb, text="⏸  MISSION PAUSED", font=("Segoe UI", 18, "bold"), foreground="#F59E0B").pack(pady=10)
            ttk.Button(pb, text="▶  Resume Mission (P)", style="Action.TButton", command=self.toggle_pause).pack(pady=6)
            ttk.Button(pb, text="✖  Abort to Title", command=self.show_title_screen).pack(pady=6)
        elif self.engine.state == GameState.PAUSED:
            self.engine.resume(); self.show_gameplay_screen()

    def show_game_over_screen(self) -> None:
        self._clear(); self.feedback_active = False
        s = self.engine.get_summary() if self.engine else None
        gb = ttk.Frame(self.main_container, padding="24", style="Card.TFrame"); gb.pack(fill=tk.BOTH, expand=True, pady=16)
        t = "🏆  MISSION ACCOMPLISHED!" if (s and s.victory) else "💥  SHIELDS DEPLETED: EMERGENCY WARP OUT"
        c = "#10B981" if (s and s.victory) else "#EF4444"
        ttk.Label(gb, text=t, font=("Segoe UI", 16, "bold"), foreground=c).pack(pady=(0, 10))
        if s:
            gr = ttk.Frame(gb); gr.pack(fill=tk.X, pady=10)
            for r, (l, v) in enumerate([("Final Score:", f"{s.score:,}"), ("Accuracy:", f"{s.accuracy_percentage}%"), ("Cleared:", f"{s.questions_solved}/{s.questions_attempted}"), ("Rank:", s.rank)]):
                ttk.Label(gr, text=l, font=("Segoe UI", 11)).grid(row=r, column=0, sticky=tk.W, pady=3)
                ttk.Label(gr, text=v, font=("Segoe UI", 11, "bold"), foreground="#38BDF8").grid(row=r, column=1, sticky=tk.W, padx=12, pady=3)
        ttk.Button(gb, text="🔄  Launch Another Expedition", style="Action.TButton", command=self.show_title_screen).pack(pady=(20, 0))

def main() -> None:
    root = tk.Tk()
    app = SpaceFractionsApp(root)
    root.mainloop()

if __name__ == "__main__": main()
