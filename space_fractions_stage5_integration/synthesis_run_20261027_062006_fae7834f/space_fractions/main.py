import re
import tkinter as tk
from tkinter import ttk
from typing import Optional
from space_fractions.game_engine import GameEngine, GameState, TurnResult
from space_fractions.ui_screens import TitleScreen, QuestionScreen, GameOverScreen


class SpaceFractionsApp:
    def __init__(self, root: tk.Tk):
        root.title('Space Fractions')
        root.geometry('800x600')
        self.container = ttk.Frame(root)
        self.container.pack(fill=tk.BOTH, expand=True)

        self.title_screen = TitleScreen(on_start=self.start_game)
        self.question_screen = QuestionScreen(
            on_answer=self.handle_answer,
            on_next=self.handle_next,
            on_pause=self.toggle_pause,
        )
        self.game_over_screen = GameOverScreen(
            on_restart=self.start_game,
            on_title=self.show_title,
        )

        self.engine: Optional[GameEngine] = None
        self.last_res: Optional[TurnResult] = None

        self.show_title()

    def _switch(self, frame: ttk.Frame):
        for screen in (self.title_screen, self.question_screen, self.game_over_screen):
            screen.pack_forget()
        frame.pack(fill=tk.BOTH, expand=True)

    def show_title(self):
        self.engine = None
        self.last_res = None
        self._switch(self.title_screen)

    def start_game(self):
        self.engine = GameEngine()
        self.engine.play()
        self._switch(self.question_screen)
        self.display_question()

    def display_question(self):
        if not self.engine or not self.engine.current_question:
            self.show_game_over()
            return
        q = self.engine.current_question
        self.question_screen.display_question(q)
        self.question_screen.update_hud(
            self.engine.shields, self.engine.score, self.engine.level, self.engine.streak
        )
        m = re.search(r'(\d+)/(\d+)', getattr(q, 'prompt', ''))
        if m:
            n, d = int(m.group(1)), int(m.group(2))
            if 0 <= n <= d and d > 0:
                self.question_screen.visualizer.draw_fraction(n, d, 'Target')
            else:
                self.question_screen.visualizer.delete('all')
        else:
            self.question_screen.visualizer.delete('all')

    def handle_answer(self, idx: int):
        if self.engine and self.engine.state == GameState.PLAYING:
            self.last_res = self.engine.submit_answer(idx)
            self.question_screen.show_feedback(
                self.last_res.is_correct, self.last_res.explanation
            )
            self.question_screen.update_hud(
                self.engine.shields, self.engine.score, self.engine.level, self.engine.streak
            )

    def handle_next(self):
        if not self.engine:
            self.show_game_over()
            return
        if self.engine.state == GameState.GAMEOVER or (
            self.last_res and self.last_res.is_game_over
        ):
            self.show_game_over()
        elif self.engine.state == GameState.PLAYING:
            if self.engine.next_question() is None or self.engine.state == GameState.GAMEOVER:
                self.show_game_over()
            else:
                self.display_question()

    def toggle_pause(self):
        if self.engine and self.engine.state in (GameState.PLAYING, GameState.PAUSED):
            if self.engine.state == GameState.PLAYING:
                self.engine.pause()
            else:
                self.engine.resume()

    def show_game_over(self):
        self._switch(self.game_over_screen)
        self.game_over_screen.display_results(
            self.engine.score,
            self.engine.questions_answered,
            self.engine.correct_answers,
        )


def main():
    root = tk.Tk()
    SpaceFractionsApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
