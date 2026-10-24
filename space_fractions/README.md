# Space Fractions — Cosmic Fraction Explorer

**Grade 6 Interactive Mathematics Learning Game**  
*Task 1 Submission Component | Autonomous Code Agent Deliverable*

---

## 1. Local Launch Instructions

Space Fractions is engineered as a **100% self-contained, dependency-free** educational game. It requires **zero external packages**, zero npm/pip dependencies, zero internet connection, and zero build steps.

### Method A: Direct Double-Click (Recommended)
1. Navigate to the `space_fractions/` folder in Windows Explorer.
2. Double-click [`index.html`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/index.html).
3. The game will launch immediately in your default modern web browser (Edge, Chrome, Firefox).

### Method B: Local Static Server (Optional)
If running in a local web development environment:
```powershell
# From the Task1 directory:
python -m http.server 8000 --directory space_fractions
```
Then visit `http://localhost:8000` in any web browser.

---

## 2. Learning Objectives & Curriculum Alignment

The game directly targets **Common Core State Standards for Grade 6 Mathematics (CCSS.MATH.CONTENT.6.NS)**:

- **Equivalent Fractions & Simplification (Level 1 - Cadet):**
  - Understanding fraction equivalence as scaling by a common factor $a/b = (a \times k)/(b \times k)$.
  - Reducing fractions to simplest form by dividing by the greatest common divisor $\gcd(a, b)$.
  - Visualizing fractions through dynamic SVG pie charts.
- **Fraction Comparisons & Common Denominators (Level 2 - Pilot):**
  - Comparing rational numbers using cross-multiplication and common denominators.
  - Adding and subtracting fractions with identical denominators.
  - Visualizing fractions via partitioned fraction bars.
- **Unlike Denominators & Mixed Arithmetic (Level 3 - Commander):**
  - Computing the least common multiple $\operatorname{lcm}(d_1, d_2)$ to establish a common denominator.
  - Performing multi-step addition and subtraction.
  - Detailed step-by-step mathematical explanations after every question to reinforce learning.

---

## 3. Game Controls & Accessibility

- **Mouse / Touch:** Click any of the 4 answer choices.
- **Keyboard Shortcuts:**
  - `1`, `2`, `3`, `4` or `A`, `B`, `C`, `D`: Select corresponding answer option.
  - `Space` or `Enter`: Advance to the next question / Start game.
  - `P` or `Escape`: Pause / Resume mission.
- **Sound Effects:** Procedural Web Audio API sound synthesizer (100% offline; zero external audio files).

---

## 4. Project Structure

```text
space_fractions/
├── index.html            # Primary responsive HTML5 game shell & HUD
├── style.css             # Pure CSS3 space styling (dark theme, zero external fonts)
├── fraction_engine.js    # Mathematical engine (Fraction class, Mulberry32 PRNG, SVG renderers)
├── game.js               # State machine (Playing, Paused, GameOver) and event handlers
├── fraction_math.py      # Python reference mathematical engine (for offline automated testing)
├── README.md             # This document
└── TRACEABILITY.md       # Architectural traceability matrix & explicit omissions
```

---

## 5. Architectural Reconciliation & Explicit Omissions

In accordance with the project directives, we explicitly distinguish between the **standalone local desktop game** and the **cloud microservices architecture** specified in `Architecture_Documentation.md`:

### What IS Implemented in the Primary Game
- **Game State Machine:** Implements `StateDiagram` transitions (`Playing <-> Paused`, `Playing -> GameOver`).
- **Core Entities:** Reflects `ClassDiagram` entities (`Game`, `Question`, `User`) and `SequenceDiagram1` flows (`play`, `getPrompt`, `submitAnswer`, `displayResult`).
- **Dynamic Question Generator:** Aligned with Grade 6 fraction arithmetic.
- **Visual Fraction Slices:** SVG pie charts and fraction bars.

### What is NOT Deployed in the Local Game (Specification Only)
The canonical `Architecture_Documentation.md` outlines enterprise cloud microservices (Node.js/Express, PostgreSQL, Redis, RabbitMQ, Docker, Kubernetes). For the primary Windows desktop delivery:
- **Cloud Infrastructure (Kubernetes, Docker):** Not deployed locally. Provided as static engineering specifications under `/specs/k8s/spacefractions-deployment.yaml`.
- **Database & Cache (PostgreSQL, Redis):** Not deployed locally. The standalone client uses a seeded PRNG for deterministic question set reproducibility; enterprise database persistence is provided as schema specifications under `/specs/sql/game_ddl.sql`.
- **Messaging (RabbitMQ):** Not deployed locally. The game operates offline with in-memory state.
- **Remote APIs (OpenAPI, gRPC Proto):** Not running as network listeners. Preserved as interface specifications under `/specs/openapi.yaml` and `/specs/internal.proto`.
