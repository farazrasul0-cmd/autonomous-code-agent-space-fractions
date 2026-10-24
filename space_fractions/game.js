/**
 * Space Fractions — Game Controller & State Machine.
 * 
 * Implements the state machine and sequence flows from Architecture_View.md:
 * - StateDiagram: Playing <-> Paused, Playing -> GameOver.
 * - SequenceDiagram1: play() -> getPrompt() -> display -> submit answer -> check answer -> display result.
 * - ClassDiagram: Game, Question, User entities.
 * 
 * Zero external dependencies. Procedural Web Audio API sound effects.
 */

class SpaceFractionsGame {
  constructor() {
    this.state = "TITLE"; // TITLE, PLAYING, PAUSED, GAMEOVER
    this.score = 0;
    this.shields = 3;
    this.streak = 0;
    this.currentLevel = 1;
    this.currentQuestionIndex = 0;
    this.totalQuestions = 10;
    this.questionsSolved = 0;
    this.currentQuestion = null;
    this.questionGenerator = new QuestionGenerator(Date.now());
    this.audioContext = null;

    // UI Cache
    this.dom = {
      screenStart: document.getElementById("screen-start"),
      screenPlay: document.getElementById("screen-play"),
      screenPause: document.getElementById("screen-pause"),
      screenGameOver: document.getElementById("screen-gameover"),
      hudStats: document.getElementById("hud-stats"),
      hudLevel: document.getElementById("hud-level"),
      hudScore: document.getElementById("hud-score"),
      hudShields: document.getElementById("hud-shields"),
      questionCounter: document.getElementById("question-counter"),
      sectorTopic: document.getElementById("sector-topic"),
      streakCounter: document.getElementById("streak-counter"),
      questionPrompt: document.getElementById("question-prompt"),
      visualRep: document.getElementById("visual-representation"),
      optionsGrid: document.getElementById("options-grid"),
      feedbackBanner: document.getElementById("feedback-banner"),
      feedbackTitle: document.getElementById("feedback-title"),
      feedbackExplanation: document.getElementById("feedback-explanation"),
      btnNextQuestion: document.getElementById("btn-next-question"),
      btnStartGame: document.getElementById("btn-start-game"),
      btnPause: document.getElementById("btn-pause"),
      btnResume: document.getElementById("btn-resume"),
      btnRestartPause: document.getElementById("btn-restart-from-pause"),
      btnPlayAgain: document.getElementById("btn-play-again"),
      selectDifficulty: document.getElementById("select-difficulty"),
      finalScore: document.getElementById("final-score"),
      finalAccuracy: document.getElementById("final-accuracy"),
      finalSolved: document.getElementById("final-solved"),
      finalRank: document.getElementById("final-rank"),
      gameoverBadge: document.getElementById("gameover-badge"),
      gameoverHeading: document.getElementById("gameover-heading"),
      gameoverMessage: document.getElementById("gameover-message"),
    };

    this._bindEvents();
  }

  _initAudio() {
    if (!this.audioContext) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioContext = new AudioContextClass();
      }
    }
  }

  /**
   * Procedural synthesizer for offline space sound effects.
   */
  _playSound(type) {
    if (!this.audioContext) return;
    try {
      const ctx = this.audioContext;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      const now = ctx.currentTime;
      if (type === "correct") {
        osc.type = "sine";
        osc.frequency.setValueAtTime(523.25, now); // C5
        osc.frequency.exponentialRampToValueAtTime(659.25, now + 0.1); // E5
        osc.frequency.exponentialRampToValueAtTime(783.99, now + 0.2); // G5
        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.35);
        osc.start(now);
        osc.stop(now + 0.35);
      } else if (type === "wrong") {
        osc.type = "sawtooth";
        osc.frequency.setValueAtTime(220, now);
        osc.frequency.exponentialRampToValueAtTime(146.83, now + 0.25);
        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.3);
        osc.start(now);
        osc.stop(now + 0.3);
      } else if (type === "gameover") {
        osc.type = "triangle";
        osc.frequency.setValueAtTime(300, now);
        osc.frequency.linearRampToValueAtTime(100, now + 0.6);
        gain.gain.setValueAtTime(0.3, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.6);
        osc.start(now);
        osc.stop(now + 0.6);
      } else if (type === "click") {
        osc.type = "sine";
        osc.frequency.setValueAtTime(800, now);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.05);
        osc.start(now);
        osc.stop(now + 0.05);
      }
    } catch (e) {
      // Audio context policy safe fallback
    }
  }

  _bindEvents() {
    this.dom.btnStartGame.addEventListener("click", () => {
      this._initAudio();
      this.currentLevel = parseInt(this.dom.selectDifficulty.value, 10) || 1;
      this.play();
    });

    this.dom.btnPause.addEventListener("click", () => this.pause());
    this.dom.btnResume.addEventListener("click", () => this.resume());
    this.dom.btnRestartPause.addEventListener("click", () => this.resetToTitle());
    this.dom.btnPlayAgain.addEventListener("click", () => this.play());
    this.dom.btnNextQuestion.addEventListener("click", () => this.nextQuestion());

    // Options click delegation
    this.dom.optionsGrid.addEventListener("click", (e) => {
      const btn = e.target.closest(".option-btn");
      if (btn && !btn.disabled) {
        const index = parseInt(btn.getAttribute("data-index"), 10);
        this.submitAnswer(index);
      }
    });

    // Global keyboard accessibility
    window.addEventListener("keydown", (e) => this._handleKeyboard(e));
  }

  _handleKeyboard(e) {
    if (this.state === "PLAYING") {
      if (e.key === "p" || e.key === "P" || e.key === "Escape") {
        this.pause();
        return;
      }

      // Hotkeys 1-4 or A-D for options
      if (!this.dom.feedbackBanner.classList.contains("hidden")) {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          this.nextQuestion();
        }
        return;
      }

      const keyMap = {
        "1": 0, "a": 0, "A": 0,
        "2": 1, "b": 1, "B": 1,
        "3": 2, "c": 2, "C": 2,
        "4": 3, "d": 3, "D": 3,
      };

      if (keyMap.hasOwnProperty(e.key)) {
        this.submitAnswer(keyMap[e.key]);
      }
    } else if (this.state === "PAUSED") {
      if (e.key === "p" || e.key === "P" || e.key === "Escape") {
        this.resume();
      }
    } else if (this.state === "TITLE") {
      if (e.key === "Enter") {
        this._initAudio();
        this.play();
      }
    } else if (this.state === "GAMEOVER") {
      if (e.key === "Enter") {
        this.play();
      }
    }
  }

  /**
   * SequenceDiagram1: play() -> display prompt
   */
  play() {
    this.state = "PLAYING";
    this.score = 0;
    this.shields = 3;
    this.streak = 0;
    this.currentQuestionIndex = 0;
    this.questionsSolved = 0;
    this.questionGenerator = new QuestionGenerator(Date.now());

    this._showScreen("screen-play");
    this.dom.hudStats.classList.remove("hidden");
    this._updateHUD();
    this.nextQuestion();
  }

  pause() {
    if (this.state !== "PLAYING") return;
    this.state = "PAUSED";
    this.dom.screenPause.classList.remove("hidden");
    this._playSound("click");
  }

  resume() {
    if (this.state !== "PAUSED") return;
    this.state = "PLAYING";
    this.dom.screenPause.classList.add("hidden");
    this._playSound("click");
  }

  resetToTitle() {
    this.state = "TITLE";
    this.dom.screenPause.classList.add("hidden");
    this.dom.hudStats.classList.add("hidden");
    this._showScreen("screen-start");
  }

  nextQuestion() {
    if (this.currentQuestionIndex >= this.totalQuestions) {
      this.gameOver(true);
      return;
    }

    this.currentQuestionIndex++;
    this.currentQuestion = this.questionGenerator.generateQuestion(
      this.currentLevel,
      this.currentQuestionIndex
    );

    // Hide feedback banner and enable option buttons
    this.dom.feedbackBanner.classList.add("hidden");
    this.dom.feedbackBanner.classList.remove("correct", "wrong");

    // Render Question
    this.dom.questionCounter.textContent = `Question ${this.currentQuestionIndex} of ${this.totalQuestions}`;
    this.dom.sectorTopic.textContent = `Topic: ${this.currentQuestion.topic}`;
    this.dom.questionPrompt.textContent = this.currentQuestion.prompt;
    this.dom.visualRep.innerHTML = this.currentQuestion.visualSvg;

    // Render Options
    const buttons = this.dom.optionsGrid.querySelectorAll(".option-btn");
    buttons.forEach((btn, idx) => {
      btn.disabled = false;
      btn.classList.remove("correct", "incorrect");
      const optText = btn.querySelector(".option-text");
      if (optText && this.currentQuestion.options[idx]) {
        optText.textContent = this.currentQuestion.options[idx];
      }
    });

    this._updateHUD();
  }

  /**
   * SequenceDiagram1: submit answer -> check answer -> return result -> display result
   */
  submitAnswer(selectedIndex) {
    if (!this.currentQuestion) return;

    // Disable all option buttons
    const buttons = this.dom.optionsGrid.querySelectorAll(".option-btn");
    buttons.forEach((btn) => (btn.disabled = true));

    const isCorrect = selectedIndex === this.currentQuestion.correctIndex;

    // Style buttons
    buttons.forEach((btn, idx) => {
      if (idx === this.currentQuestion.correctIndex) {
        btn.classList.add("correct");
      } else if (idx === selectedIndex && !isCorrect) {
        btn.classList.add("incorrect");
      }
    });

    if (isCorrect) {
      this.streak++;
      const streakBonus = (this.streak > 1) ? (this.streak - 1) * 20 : 0;
      this.score += 100 + streakBonus;
      this.questionsSolved++;
      this._playSound("correct");

      this.dom.feedbackBanner.className = "feedback-banner correct";
      this.dom.feedbackTitle.textContent = `✓ Recalibration Success! (+${100 + streakBonus} Energy)`;
      this.dom.feedbackExplanation.textContent = this.currentQuestion.explanation;
    } else {
      this.streak = 0;
      this.shields--;
      this._playSound("wrong");

      this.dom.feedbackBanner.className = "feedback-banner wrong";
      this.dom.feedbackTitle.textContent = `⚠ Recalibration Anomaly Detected! (Shields -1)`;
      this.dom.feedbackExplanation.textContent = `Correct solution: ${this.currentQuestion.options[this.currentQuestion.correctIndex]}. ${this.currentQuestion.explanation}`;

      if (this.shields <= 0) {
        this.dom.btnNextQuestion.textContent = "View Debriefing [Space / Enter]";
      } else {
        this.dom.btnNextQuestion.textContent = "Next Challenge [Space / Enter]";
      }
    }

    this._updateHUD();
    this.dom.feedbackBanner.classList.remove("hidden");
    this.dom.btnNextQuestion.focus();

    if (this.shields <= 0) {
      this.dom.btnNextQuestion.onclick = () => this.gameOver(false);
    } else {
      this.dom.btnNextQuestion.onclick = () => this.nextQuestion();
    }
  }

  /**
   * StateDiagram: gameOver()
   */
  gameOver(victory = true) {
    this.state = "GAMEOVER";
    this._showScreen("screen-gameover");
    this.dom.hudStats.classList.add("hidden");

    if (victory) {
      this._playSound("correct");
      this.dom.gameoverBadge.className = "space-badge victory";
      this.dom.gameoverBadge.textContent = "MISSION SUCCESS";
      this.dom.gameoverHeading.textContent = "Sector Cleared, Commander!";
      this.dom.gameoverMessage.textContent = "Your fraction calculations safely navigated the fleet through the asteroid belt.";
    } else {
      this._playSound("gameover");
      this.dom.gameoverBadge.className = "space-badge";
      this.dom.gameoverBadge.textContent = "SHIELDS DEPLETED";
      this.dom.gameoverHeading.textContent = "Emergency Warp Out";
      this.dom.gameoverMessage.textContent = "Your spacecraft ran out of shields. Review your fraction ratios and try again!";
    }

    const accuracy = this.currentQuestionIndex > 0
      ? Math.round((this.questionsSolved / this.currentQuestionIndex) * 100)
      : 0;

    let rank = "Ensign";
    if (accuracy >= 90) rank = "Fleet Admiral";
    else if (accuracy >= 75) rank = "Chief Navigator";
    else if (accuracy >= 50) rank = "Flight Lieutenant";

    this.dom.finalScore.textContent = this.score;
    this.dom.finalAccuracy.textContent = `${accuracy}%`;
    this.dom.finalSolved.textContent = `${this.questionsSolved} / ${this.currentQuestionIndex}`;
    this.dom.finalRank.textContent = rank;
  }

  _updateHUD() {
    this.dom.hudLevel.textContent = `Level ${this.currentLevel}`;
    this.dom.hudScore.textContent = this.score;

    let shieldIcons = "";
    for (let i = 0; i < 3; i++) {
      shieldIcons += (i < this.shields) ? "🛡️ " : "❌ ";
    }
    this.dom.hudShields.textContent = shieldIcons.trim();
    this.dom.streakCounter.textContent = `Streak: ${this.streak} 🔥`;
  }

  _showScreen(screenId) {
    const screens = [
      this.dom.screenStart,
      this.dom.screenPlay,
      this.dom.screenPause,
      this.dom.screenGameOver,
    ];
    screens.forEach((s) => {
      if (s.id === screenId) {
        s.classList.remove("hidden");
        s.classList.add("active");
      } else if (s.id !== "screen-pause") {
        s.classList.add("hidden");
        s.classList.remove("active");
      }
    });
  }
}

// Auto-instantiate when DOM is loaded in browser
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    window.gameInstance = new SpaceFractionsGame();
    try {
      const params = new URLSearchParams(window.location.search);
      const mode = params.get("mode");
      if (mode === "play") {
        window.gameInstance.play();
      } else if (mode === "answer") {
        window.gameInstance.play();
        window.gameInstance.submitAnswer(window.gameInstance.currentQuestion.correctIndex);
      } else if (mode === "pause") {
        window.gameInstance.play();
        window.gameInstance.pause();
      } else if (mode === "gameover") {
        window.gameInstance.play();
        window.gameInstance.gameOver(true);
      }
    } catch (e) {
      // Ignore URL parsing errors in restricted environments
    }
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { SpaceFractionsGame };
}
