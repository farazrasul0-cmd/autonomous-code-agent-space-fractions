import math
import tkinter as tk
from tkinter import ttk
from typing import Optional


class FractionVisualizer(tk.Canvas):
    def __init__(self, parent: tk.Widget, width: int = 220, height: int = 150, **kwargs):
        super().__init__(parent, width=width, height=height, bg='#111827', highlightthickness=0, **kwargs)
        self.canvas_width, self.canvas_height = width, height

    def draw_fraction(self, numerator: int, denominator: int, title: str = '') -> None:
        self.delete('all')
        if denominator <= 0 or numerator < 0:
            self.create_text(self.canvas_width // 2, self.canvas_height // 2, text='Invalid Fraction',
                             fill='#EF4444', font=('Segoe UI', 10, 'bold'))
            return
        cx, cy = self.canvas_width // 2, (self.canvas_height // 2) - 10
        r = min(cx, cy) - 12
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill='#1F2937', outline='#38BDF8', width=2)
        eff = min(numerator, denominator)
        if eff > 0:
            angle = 360.0 / denominator
            for i in range(eff):
                self.create_arc(cx - r, cy - r, cx + r, cy + r, start=90.0 - (i + 1) * angle,
                                extent=angle, fill='#06B6D4', outline='#38BDF8')
        lbl = f'{title}: {numerator}/{denominator}' if title else f'{numerator}/{denominator}'
        self.create_text(cx, self.canvas_height - 12, text=lbl, fill='#F8FAFC', font=('Segoe UI', 10, 'bold'))


class HUDFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, padding='8', **kwargs)
        self.shields_var = tk.StringVar(value='SHIELDS: 3')
        self.score_var = tk.StringVar(value='SCORE: 0')
        self.streak_var = tk.StringVar(value='STREAK: 0')
        self.progress_var = tk.StringVar(value='1 / 10')
        self.level_var = tk.StringVar(value='Level 1')
        ttk.Label(self, textvariable=self.shields_var, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=8)
        ttk.Label(self, textvariable=self.score_var, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=8)
        ttk.Label(self, textvariable=self.streak_var, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=8)
        ttk.Label(self, textvariable=self.progress_var, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=8)
        ttk.Label(self, textvariable=self.level_var, font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT, padx=8)

    def update_hud(
        self,
        shields: int = 3,
        score: int = 0,
        *args,
        level: Optional[int] = None,
        status: str = '',
        streak: Optional[int] = None,
        current_q: Optional[int] = None,
        total_q: Optional[int] = None,
        **kwargs,
    ) -> None:
        if len(args) == 4:
            streak_val = args[0]
            curr_q_val = args[1]
            tot_q_val = args[2]
            lvl_val = args[3]
            stat_val = status
        elif len(args) == 2:
            lvl_val = args[0]
            stat_val = str(args[1])
            streak_val = streak if streak is not None else 0
            curr_q_val = current_q
            tot_q_val = total_q
        elif len(args) == 1:
            lvl_val = args[0]
            stat_val = status
            streak_val = streak if streak is not None else 0
            curr_q_val = current_q
            tot_q_val = total_q
        else:
            lvl_val = level if level is not None else 1
            stat_val = status
            streak_val = streak if streak is not None else 0
            curr_q_val = current_q
            tot_q_val = total_q

        if shields <= 0:
            self.shields_var.set('SHIELDS: DEPLETED')
        else:
            self.shields_var.set(f'SHIELDS: {shields}')

        self.score_var.set(f'SCORE: {score}')

        if streak_val is not None:
            self.streak_var.set(f'STREAK: {streak_val}')

        if curr_q_val is not None and tot_q_val is not None:
            self.progress_var.set(f'{curr_q_val} / {tot_q_val}')

        level_str = f'Level {lvl_val}'
        if stat_val:
            level_str += f' ({stat_val})'
        self.level_var.set(level_str)

from typing import Callable


class TitleScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, on_start: Optional[Callable[[], None]] = None, **kwargs) -> None:
        super().__init__(parent, padding='24', **kwargs)
        self.on_start = on_start

        ttk.Label(self, text='SPACE FRACTIONS', font=('Segoe UI', 20, 'bold')).pack(pady=(0, 8))
        ttk.Label(self, text='Grade 6 Cosmic Mathematics Learning Expedition').pack(pady=(0, 16))

        def launch() -> None:
            if callable(self.on_start):
                self.on_start()

        ttk.Button(self, text='Launch Mission', command=launch).pack()


from typing import Any


class QuestionScreen(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        on_answer: Optional[Callable[[int], None]] = None,
        on_next: Optional[Callable[[], None]] = None,
        on_pause: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self.hud = HUDFrame(self)
        self.hud.pack()
        ttk.Button(self, text="Pause", command=on_pause).pack()
        self.prompt_label = ttk.Label(self, text="")
        self.prompt_label.pack()
        self.visualizer = FractionVisualizer(self)
        self.visualizer.pack()
        self.option_buttons = []
        for i in range(4):
            btn = ttk.Button(self, text="", command=lambda i=i: on_answer(i) if on_answer else None)
            btn.pack()
            self.option_buttons.append(btn)
        self.feedback_label = ttk.Label(self, text="")
        self.feedback_label.pack()
        self.next_button = ttk.Button(self, text="Next", command=on_next)
        self.next_button.pack()

    def display_question(self, q: Any) -> None:
        prompt = ""
        options = []
        try:
            if isinstance(q, dict):
                prompt = str(q.get("prompt", q.get("question", "")))
                options = q.get("options", q.get("choices", []))
            else:
                prompt = str(getattr(q, "prompt", getattr(q, "question", "")))
                options = getattr(q, "options", getattr(q, "choices", []))
        except Exception:
            prompt = ""
            options = []
        if not isinstance(options, list):
            options = []
        self.prompt_label.config(text=prompt)
        for i, btn in enumerate(self.option_buttons):
            text = str(options[i]) if i < len(options) else ""
            btn.config(text=text, state="normal")
        self.next_button.pack_forget()
        self.feedback_label.config(text="")

    def show_feedback(self, is_correct: bool, explanation: str = "") -> None:
        for btn in self.option_buttons:
            btn.config(state="disabled")
        text = "Correct!" if is_correct else "Incorrect: " + explanation
        self.feedback_label.config(text=text)
        self.next_button.pack()

    def update_hud(self, shields: int, score: int, level: int = 1, status: str = "") -> None:
        self.hud.update_hud(shields, score, level=level, status=status)



class GameOverScreen(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        on_restart: Optional[Callable[[], None]] = None,
        on_title: Optional[Callable[[], None]] = None,
        **kwargs
    ) -> None:
        super().__init__(parent, **kwargs)
        self._on_restart = on_restart
        self._on_title = on_title

        self._summary_label = ttk.Label(self, text="Mission Summary")
        self._summary_label.pack(pady=10)

        self._score_label = ttk.Label(self, text="Final Score: 0")
        self._score_label.pack(pady=5)

        self._questions_label = ttk.Label(self, text="Questions Solved: 0/0")
        self._questions_label.pack(pady=5)

        restart_btn = ttk.Button(self, text="Play Again", command=self._on_restart)
        restart_btn.pack(pady=5)

        title_btn = ttk.Button(self, text="Main Menu", command=self._on_title)
        title_btn.pack(pady=5)

    def display_results(
        self,
        score: int = 0,
        questions_answered: int = 0,
        correct_answers: int = 0,
        **kwargs
    ) -> None:
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0

        try:
            questions_answered = int(questions_answered)
        except (TypeError, ValueError):
            questions_answered = 0

        try:
            correct_answers = int(correct_answers)
        except (TypeError, ValueError):
            correct_answers = 0

        self._score_label.config(text="Final Score: {}".format(score))
        self._questions_label.config(
            text="Questions Solved: {}/{}".format(correct_answers, questions_answered)
        )
