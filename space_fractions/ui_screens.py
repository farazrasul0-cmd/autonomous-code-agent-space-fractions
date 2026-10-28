"""Space Fractions — Cosmic Mathematics UI Screens.

Provides polished, high-contrast, space-themed desktop user interfaces
designed for Grade 6 educational exploration:
1. FractionVisualizer: Canvas-based orbital radar displaying geometric fraction sectors.
2. HUDFrame: Elevated cockpit dashboard displaying Shields, Energy, Streak, and Sector.
3. TitleScreen: High-impact briefing screen with mission directives and Launch Mission action.
4. QuestionScreen: Spacious 2x2 answer grid, cosmic challenge card, pause overlay, and feedback.
5. GameOverScreen: Debrief summary card with final score, accuracy %, rank honor, and actions.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional


def setup_cosmic_theme(style: Optional[ttk.Style] = None) -> ttk.Style:
    """Configures cosmic learning theme with dark deep-space palette and elevated cards."""
    if style is None:
        style = ttk.Style()

    # Palette
    BG_DEEP = "#0B0F19"
    SURFACE_CARD = "#111827"
    BORDER_SUBTLE = "#1E293B"
    CYAN_ACCENT = "#06B6D4"
    CYAN_LIGHT = "#38BDF8"
    TEXT_WHITE = "#F8FAFC"
    TEXT_MUTED = "#94A3B8"
    GOLD_ACCENT = "#F59E0B"
    EMERALD_GREEN = "#10B981"
    CRIMSON_RED = "#EF4444"

    style.configure("Cosmic.TFrame", background=BG_DEEP)
    style.configure("Card.TFrame", background=SURFACE_CARD, relief="solid", borderwidth=1)
    style.configure("HUDCard.TFrame", background=SURFACE_CARD, relief="solid", borderwidth=1)

    style.configure("CosmicTitle.TLabel", font=("Segoe UI", 22, "bold"), foreground=CYAN_LIGHT, background=BG_DEEP)
    style.configure("CosmicSub.TLabel", font=("Segoe UI", 11), foreground=TEXT_MUTED, background=BG_DEEP)
    style.configure("CardHeader.TLabel", font=("Segoe UI", 8, "bold"), foreground=TEXT_MUTED, background=SURFACE_CARD)
    style.configure("CardValue.TLabel", font=("Segoe UI", 11, "bold"), foreground=TEXT_WHITE, background=SURFACE_CARD)
    style.configure("CardTitle.TLabel", font=("Segoe UI", 12, "bold"), foreground=CYAN_ACCENT, background=SURFACE_CARD)
    style.configure("CardText.TLabel", font=("Segoe UI", 10), foreground="#E2E8F0", background=SURFACE_CARD)
    style.configure("Prompt.TLabel", font=("Segoe UI", 13, "bold"), foreground=TEXT_WHITE, background=SURFACE_CARD)
    style.configure("Feedback.TLabel", font=("Segoe UI", 11, "bold"), background=SURFACE_CARD)

    style.configure(
        "PrimaryAction.TButton",
        font=("Segoe UI", 11, "bold"),
        background="#0284C7",
        foreground="#FFFFFF",
        borderwidth=1,
        focuscolor="none",
        padding="8 16",
    )
    style.map("PrimaryAction.TButton", background=[("active", "#0369A1"), ("disabled", "#334155")])

    style.configure(
        "AnswerOption.TButton",
        font=("Segoe UI", 11, "bold"),
        background="#1E293B",
        foreground=TEXT_WHITE,
        borderwidth=1,
        bordercolor="#334155",
        focuscolor="none",
        padding="10",
    )
    style.map(
        "AnswerOption.TButton",
        background=[("active", "#0284C7"), ("disabled", "#0F172A")],
        foreground=[("disabled", "#64748B")],
    )

    style.configure(
        "Pause.TButton",
        font=("Segoe UI", 9, "bold"),
        background="#334155",
        foreground=TEXT_WHITE,
        borderwidth=1,
        focuscolor="none",
        padding="5 10",
    )
    style.map("Pause.TButton", background=[("active", "#475569")])

    style.configure(
        "Next.TButton",
        font=("Segoe UI", 11, "bold"),
        background=EMERALD_GREEN,
        foreground="#FFFFFF",
        borderwidth=1,
        focuscolor="none",
        padding="8 18",
    )
    style.map("Next.TButton", background=[("active", "#059669")])

    return style


class FractionVisualizer(tk.Canvas):
    """Orbital radar canvas displaying geometric fraction sectors with cosmic styling."""

    def __init__(self, parent: tk.Widget, width: int = 240, height: int = 150, **kwargs):
        super().__init__(parent, width=width, height=height, bg="#111827", highlightthickness=0, **kwargs)
        self.canvas_width, self.canvas_height = width, height

    def draw_fraction(self, numerator: int, denominator: int, title: str = "") -> None:
        """Renders fraction pie/radar slices on the cosmic canvas."""
        self.delete("all")
        if denominator <= 0 or numerator < 0:
            self.create_text(
                self.canvas_width // 2,
                self.canvas_height // 2,
                text="Invalid Fraction",
                fill="#EF4444",
                font=("Segoe UI", 11, "bold"),
            )
            return

        cx, cy = self.canvas_width // 2, (self.canvas_height // 2) - 10
        r = min(cx, cy) - 14

        # Background circular radar grid
        self.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4, outline="#1E293B", width=1)
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#0B0F19", outline="#0284C7", width=2)

        # Draw subtle radial grid dividers for denominator equal parts
        if denominator > 1 and denominator <= 24:
            for d_idx in range(denominator):
                rad = math.radians(90.0 - (d_idx * 360.0 / denominator))
                x_end = cx + (r * math.cos(rad))
                y_end = cy - (r * math.sin(rad))
                self.create_line(cx, cy, x_end, y_end, fill="#1E293B", width=1)

        # Draw filled fraction sectors
        eff = min(numerator, denominator)
        if eff > 0:
            angle = 360.0 / denominator
            for i in range(eff):
                self.create_arc(
                    cx - r,
                    cy - r,
                    cx + r,
                    cy + r,
                    start=90.0 - (i + 1) * angle,
                    extent=angle,
                    fill="#06B6D4",
                    outline="#38BDF8",
                    width=2,
                )

        # Center hub pip
        self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#38BDF8", outline="#06B6D4")

        # Bottom fraction badge
        lbl = f"{title}: {numerator}/{denominator}" if title else f"{numerator}/{denominator}"
        self.create_text(
            cx,
            self.canvas_height - 12,
            text=lbl,
            fill="#F8FAFC",
            font=("Segoe UI", 10, "bold"),
        )


class HUDFrame(ttk.Frame):
    """Cockpit telemetry dashboard displaying Shields, Score, Streak, and Sector."""

    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, padding="4", **kwargs)
        self.shields_var = tk.StringVar(value="SHIELDS: 3")
        self.score_var = tk.StringVar(value="SCORE: 0")
        self.streak_var = tk.StringVar(value="STREAK: 0")
        self.progress_var = tk.StringVar(value="1 / 10")
        self.level_var = tk.StringVar(value="Level 1")

        self._build_cards()

    def _build_cards(self) -> None:
        """Constructs 5 distinct telemetry card widgets."""
        cards = [
            ("SHIELDS", self.shields_var, "#38BDF8"),
            ("ENERGY", self.score_var, "#F59E0B"),
            ("STREAK", self.streak_var, "#FBBF24"),
            ("SECTOR", self.progress_var, "#38BDF8"),
            ("RANK", self.level_var, "#A855F7"),
        ]

        self.columnconfigure((0, 1, 2, 3, 4), weight=1)
        self.metric_labels: dict[str, ttk.Label] = {}

        for col, (title, var, color) in enumerate(cards):
            card = ttk.Frame(self, style="HUDCard.TFrame", padding="4 6")
            card.grid(row=0, column=col, padx=3, pady=2, sticky="nsew")

            ttk.Label(card, text=title, font=("Segoe UI", 7, "bold"), foreground="#94A3B8", background="#111827").pack(anchor=tk.W)
            val_lbl = ttk.Label(card, textvariable=var, font=("Segoe UI", 10, "bold"), foreground=color, background="#111827")
            val_lbl.pack(anchor=tk.W)
            self.metric_labels[title] = val_lbl

    def update_hud(
        self,
        shields: int = 3,
        score: int = 0,
        *args,
        level: Optional[int] = None,
        status: str = "",
        streak: Optional[int] = None,
        current_q: Optional[int] = None,
        total_q: Optional[int] = None,
        **kwargs,
    ) -> None:
        """Updates telemetry variables across all supported calling conventions."""
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

        # Shields
        if shields <= 0:
            self.shields_var.set("SHIELDS: DEPLETED")
            if "SHIELDS" in self.metric_labels:
                self.metric_labels["SHIELDS"].config(foreground="#EF4444")
        else:
            self.shields_var.set(f"SHIELDS: {shields}")
            if "SHIELDS" in self.metric_labels:
                shield_color = "#38BDF8" if shields >= 2 else "#F59E0B"
                self.metric_labels["SHIELDS"].config(foreground=shield_color)

        # Score / Energy
        self.score_var.set(f"SCORE: {score}")

        # Streak
        if streak_val is not None:
            self.streak_var.set(f"STREAK: {streak_val}")

        # Progress
        if curr_q_val is not None and tot_q_val is not None:
            self.progress_var.set(f"{curr_q_val} / {tot_q_val}")

        # Level
        level_str = f"Level {lvl_val}"
        if stat_val:
            level_str += f" ({stat_val})"
        self.level_var.set(level_str)


class TitleScreen(ttk.Frame):
    """High-impact briefing screen with mission directives and launch action."""

    def __init__(self, parent: tk.Widget, on_start: Optional[Callable[[], None]] = None, **kwargs) -> None:
        super().__init__(parent, padding="16", style="Cosmic.TFrame", **kwargs)
        self.on_start = on_start

        # Title & Subtitle Header
        header = ttk.Frame(self, style="Cosmic.TFrame")
        header.pack(fill=tk.X, pady=(16, 12))
        ttk.Label(header, text="🚀  SPACE FRACTIONS", style="CosmicTitle.TLabel").pack()
        ttk.Label(
            header,
            text="Grade 6 Cosmic Mathematics Learning Expedition",
            style="CosmicSub.TLabel",
        ).pack(pady=(4, 0))

        # Mission Directives Card
        card = ttk.Frame(self, padding="18", style="Card.TFrame")
        card.pack(fill=tk.BOTH, expand=True, pady=10)

        ttk.Label(card, text="MISSION DIRECTIVES", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))

        directives = [
            "• Pilot your spacecraft through uncharted solar systems by solving fraction challenges.",
            "• Correct solutions restore +100 Energy Units and build Star Streak multipliers.",
            "• Incorrect solutions deplete 1 Shield. Maintain shield integrity to complete the mission.",
            "• Controls: Click answers or press keys [1], [2], [3], [4]. Press [P] or [Esc] to Pause.",
        ]
        for d in directives:
            ttk.Label(card, text=d, style="CardText.TLabel", wraplength=700).pack(anchor=tk.W, pady=3)

        # Dominant Launch Action Button
        action_box = ttk.Frame(self, style="Cosmic.TFrame")
        action_box.pack(pady=(16, 8))

        def launch() -> None:
            if callable(self.on_start):
                self.on_start()

        self.btn_launch = ttk.Button(
            action_box,
            text="▶  Launch Mission",
            style="PrimaryAction.TButton",
            command=launch,
        )
        self.btn_launch.pack(ipadx=16, ipady=4)


class QuestionScreen(ttk.Frame):
    """Cosmic question cockpit with 2x2 answer grid, visualizer, pause, and feedback."""

    def __init__(
        self,
        parent: tk.Widget,
        on_answer: Optional[Callable[[int], None]] = None,
        on_next: Optional[Callable[[], None]] = None,
        on_pause: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, padding="10", style="Cosmic.TFrame", **kwargs)
        self.on_answer = on_answer
        self.on_next = on_next
        self.on_pause = on_pause

        # Top Control & HUD Bar
        top_bar = ttk.Frame(self, style="Cosmic.TFrame")
        top_bar.pack(fill=tk.X, pady=(0, 6))

        self.hud = HUDFrame(top_bar)
        self.hud.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.pause_button = ttk.Button(top_bar, text="Pause", style="Pause.TButton", command=on_pause)
        self.pause_button.pack(side=tk.RIGHT, padx=(8, 0), pady=4)

        # Pause Notification Banner
        self.pause_label = ttk.Label(
            self,
            text="⏸  MISSION PAUSED — Systems on Standby (Press P to Resume)",
            font=("Segoe UI", 12, "bold"),
            foreground="#F59E0B",
            background="#0B0F19",
        )
        self.is_paused = False

        # Central Cockpit Card
        self.card = ttk.Frame(self, padding="14", style="Card.TFrame")
        self.card.pack(fill=tk.BOTH, expand=True, pady=6)

        self.prompt_label = ttk.Label(self.card, text="", style="Prompt.TLabel", wraplength=740)
        self.prompt_label.pack(anchor=tk.W, pady=(0, 8))

        self.visualizer = FractionVisualizer(self.card, width=240, height=140)
        self.visualizer.pack(pady=4)

        # 2x2 Answer Choices Grid
        self.grid_frame = ttk.Frame(self.card, style="Card.TFrame")
        self.grid_frame.pack(fill=tk.X, pady=(8, 4))
        self.grid_frame.columnconfigure(0, weight=1)
        self.grid_frame.columnconfigure(1, weight=1)

        self.option_buttons = []
        for i in range(4):
            btn = ttk.Button(
                self.grid_frame,
                text="",
                style="AnswerOption.TButton",
                command=lambda idx=i: self._on_btn_click(idx),
            )
            btn.grid(row=i // 2, column=i % 2, padx=6, pady=5, sticky="nsew")
            self.option_buttons.append(btn)

        # Feedback & Navigation Strip
        self.nav_strip = ttk.Frame(self, style="Cosmic.TFrame")
        self.nav_strip.pack(fill=tk.X, pady=(6, 0))

        self.feedback_label = ttk.Label(
            self.nav_strip,
            text="",
            font=("Segoe UI", 11, "bold"),
            background="#0B0F19",
            wraplength=640,
        )
        self.feedback_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.next_button = ttk.Button(
            self.nav_strip,
            text="Next Challenge ▶",
            style="Next.TButton",
            command=on_next,
        )
        self.next_button.pack(side=tk.RIGHT)
        self.next_button.pack_forget()

    def _on_btn_click(self, idx: int) -> None:
        if callable(self.on_answer) and not self.is_paused:
            self.on_answer(idx)

    def display_question(self, q: Any) -> None:
        """Presents a new question challenge and resets button states."""
        self.set_paused(False)
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
        """Displays formatted turn feedback and presents the Next action."""
        for btn in self.option_buttons:
            btn.config(state="disabled")

        if is_correct:
            self.feedback_label.config(
                text="✨  TARGET RESOLVED! (+100 Energy Units Awarded)",
                foreground="#10B981",
            )
        else:
            fb_text = "⚠️  CALCULATION DISCREPANCY (-1 Shield Lost)"
            if explanation:
                fb_text += f"\n{explanation}"
            self.feedback_label.config(text=fb_text, foreground="#EF4444")

        self.next_button.pack(side=tk.RIGHT, padx=4)

    def set_paused(self, is_paused: bool) -> None:
        """Updates UI elements between active play and paused states."""
        self.is_paused = bool(is_paused)
        if self.is_paused:
            self.pause_button.config(text="Resume")
            self.pause_label.pack(after=self.pause_button.master, pady=4)
            for btn in self.option_buttons:
                btn.config(state="disabled")
        else:
            self.pause_button.config(text="Pause")
            self.pause_label.pack_forget()
            if not self.feedback_label.cget("text"):
                for btn in self.option_buttons:
                    btn.config(state="normal")

    def update_hud(self, shields: int, score: int, level: int = 1, status: str = "") -> None:
        """Passes telemetry updates to the nested HUD frame."""
        self.hud.update_hud(shields, score, level=level, status=status)


class GameOverScreen(ttk.Frame):
    """Mission debrief summary card with final score, accuracy %, rank, and actions."""

    def __init__(
        self,
        parent: tk.Widget,
        on_restart: Optional[Callable[[], None]] = None,
        on_title: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, padding="18", style="Cosmic.TFrame", **kwargs)
        self._on_restart = on_restart
        self._on_title = on_title

        # Debrief Card
        self.card = ttk.Frame(self, padding="20", style="Card.TFrame")
        self.card.pack(fill=tk.BOTH, expand=True, pady=10)

        self._summary_label = ttk.Label(self.card, text="Mission Summary", style="CosmicTitle.TLabel")
        self._summary_label.pack(pady=(0, 14))

        stats_box = ttk.Frame(self.card, style="Card.TFrame")
        stats_box.pack(fill=tk.X, pady=8)

        self._score_label = ttk.Label(
            stats_box,
            text="Final Score: 0",
            font=("Segoe UI", 12, "bold"),
            foreground="#F59E0B",
            background="#111827",
        )
        self._score_label.pack(pady=4)

        self._questions_label = ttk.Label(
            stats_box,
            text="Questions Solved: 0/0",
            font=("Segoe UI", 11, "bold"),
            foreground="#38BDF8",
            background="#111827",
        )
        self._questions_label.pack(pady=4)

        self._accuracy_label = ttk.Label(
            stats_box,
            text="Accuracy: 0%",
            font=("Segoe UI", 11, "bold"),
            foreground="#10B981",
            background="#111827",
        )
        self._accuracy_label.pack(pady=4)

        self._rank_label = ttk.Label(
            stats_box,
            text="Expedition Rank: Cadet Navigator",
            font=("Segoe UI", 11, "bold"),
            foreground="#A855F7",
            background="#111827",
        )
        self._rank_label.pack(pady=4)

        # Action Buttons
        btn_box = ttk.Frame(self.card, style="Card.TFrame")
        btn_box.pack(pady=(18, 6))

        restart_btn = ttk.Button(
            btn_box,
            text="🔄  Launch Another Expedition",
            style="PrimaryAction.TButton",
            command=self._on_restart,
        )
        restart_btn.pack(side=tk.LEFT, padx=8)

        title_btn = ttk.Button(
            btn_box,
            text="🏠  Return to Mission Control",
            style="Pause.TButton",
            command=self._on_title,
        )
        title_btn.pack(side=tk.LEFT, padx=8)

    def display_results(
        self,
        score: int = 0,
        questions_answered: int = 0,
        correct_answers: int = 0,
        **kwargs,
    ) -> None:
        """Presents calculated mission results, accuracy %, and earned rank."""
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

        pct = int(round((correct_answers / questions_answered * 100))) if questions_answered > 0 else 0

        if pct >= 90:
            rank = "Fleet Admiral"
        elif pct >= 75:
            rank = "Wing Commander"
        elif pct >= 50:
            rank = "Senior Pilot"
        else:
            rank = "Cadet Navigator"

        if score > 0 and correct_answers >= 5:
            self._summary_label.config(text="🏆  MISSION ACCOMPLISHED!", foreground="#10B981")
        else:
            self._summary_label.config(text="💥  EXPEDITION CONCLUDED — SHIELDS DEPLETED", foreground="#EF4444")

        self._score_label.config(text=f"Final Score: {score}")
        self._questions_label.config(text=f"Questions Solved: {correct_answers}/{questions_answered}")
        self._accuracy_label.config(text=f"Accuracy: {pct}%")
        self._rank_label.config(text=f"Expedition Rank: {rank}")
