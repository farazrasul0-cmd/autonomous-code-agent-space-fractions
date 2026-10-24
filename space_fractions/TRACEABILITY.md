# Space Fractions — Architectural Traceability Matrix & Implementation Evidence

This document establishes rigorous bidirectional traceability between the canonical architecture specifications (`Architecture_View.md` and `Architecture_Documentation.md`) and the primary game implementation deliverable (`space_fractions/`).

In accordance with engineering integrity principles, each item is classified with honest implementation statuses:
- **`implemented`**: Fully realized in the local game application with functional evidence.
- **`partially implemented`**: Logic or client representation implemented, but secondary aspects omitted.
- **`specification only`**: Defined as an authoritative static specification under `/specs`, but not running as an active server on the local machine.
- **`not implemented`**: Enterprise-only feature not relevant to the standalone client experience.

---

## 1. Traceability to 11 Numbered Views (`Architecture_View.md`)

| Item # | View Category | Diagram ID | Key Architecture Entities | Implementation Status | Evidence File Path(s) | Notes & Implementation Details |
| :---: | :--- | :--- | :--- | :---: | :--- | :--- |
| **1** | Scenario View | `UseCaseDiagram` | `EndUser`, `PlayGame`, `ViewScore`, `ViewHelp` | **`implemented`** | [`space_fractions/index.html`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/index.html#L35-L65)<br>[`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L140-L190) | End user launches mission, solves fraction puzzles, views live score & HUD, and views mathematical explanations. |
| **1** | Scenario View | `UseCaseDiagram` | `Admin`, `UpdateQuestions` | **`partially implemented`** | [`space_fractions/fraction_engine.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/fraction_engine.js#L120-L240) | Dynamic question generator supports programmatic difficulty & seed parameters; administrative network API is specification-only. |
| **2** | Logic View | `ClassDiagram` | `Game` (`id`, `score`, `play()`, `viewScore()`) | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L15-L50) | `SpaceFractionsGame` encapsulates score, play loop, state transitions, and HUD telemetry. |
| **2** | Logic View | `ClassDiagram` | `Question` (`id`, `prompt`, `options`, `getPrompt()`, `getOptions()`) | **`implemented`** | [`space_fractions/fraction_engine.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/fraction_engine.js#L140-L220) | Question object model with prompt, 4 randomized options, correctIndex, explanation, and visual SVG. |
| **2** | Logic View | `ClassDiagram` | `User` (`playGame()`, `viewScore()`) | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L125-L160) | Interactive event handlers capturing user actions, keyboard input, and score display. |
| **2** | Logic View | `ClassDiagram` | `Admin` (`updateQuestions()`) | **`specification only`** | [`specs/openapi.yaml`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/openapi.yaml) | Question management modeled via static OpenAPI specification. |
| **3** | Logic View | `ObjectDiagram` | `game1`, `question1`, `user1`, `admin1` | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L300-L315) | Active game instance instantiated in browser DOM with live question references. |
| **4** | Logic View | `StateDiagram` | `Playing`, `Paused`, `GameOver` | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L150-L210) | State machine with `play()`, `pause()`, `resume()`, and `gameOver(victory)` transitions. |
| **5** | Process View | `ActivityDiagram` | `play game` -> `check game over` -> `calculate score` -> `display score` | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L160-L250) | Full game flow: question presentation, answering, shield deduction or streak bonus, final score & rank calculation. |
| **6** | Process View | `SequenceDiagram1` | `User -> Game -> Question: play, getPrompt, submit answer, check answer, display result` | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L180-L245) | Exact sequence implemented: `submitAnswer(idx)` checks against `currentQuestion.correctIndex`, provides instant feedback and sound. |
| **6** | Process View | `SequenceDiagram2` | `Admin -> Question: update()` | **`specification only`** | [`specs/internal.proto`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/internal.proto) | gRPC / REST administrative interface specified in `/specs`. |
| **7** | Process View | `CollaborationDiagram1` | Play Game scenario collaboration | **`implemented`** | [`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js#L140-L245) | Seamless collaboration between UI canvas, Fraction math engine, and scoring HUD. |
| **7** | Process View | `CollaborationDiagram2` | Update Questions collaboration | **`specification only`** | [`specs/openapi.yaml`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/openapi.yaml) | Cloud question sync collaboration preserved as specification. |
| **8** | Development View | `PackageDiagram` | Packages: `Game`, `User`, `Admin` | **`implemented`** | [`space_fractions/`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/) | Modular separation into `fraction_engine.js` (domain math), `game.js` (controller/state), and `index.html` (view). |
| **9** | Development View | `ComponentDiagram` | `GameComponent`, `QuestionComponent` | **`implemented`** | [`space_fractions/`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/) | Local runtime components executing fraction logic and state management. |
| **9** | Development View | `ComponentDiagram` | `UserComponent`, `AdminComponent` | **`specification only`** | [`specs/`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/) | Enterprise authentication/administration components preserved in specifications. |
| **10** | Physical View | `DeploymentDiagram` | `GameServer`, `QuestionServer` | **`specification only`** | [`specs/k8s/spacefractions-deployment.yaml`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/k8s/spacefractions-deployment.yaml) | Server deployment manifests provided for Kubernetes cloud deployment. |
| **10** | Physical View | `DeploymentDiagram` | `UserClient` | **`implemented`** | [`space_fractions/index.html`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/index.html) | Standalone client application executing in user's desktop browser. |
| **11** | Physical View | `ContainerDiagram` | `GameContainer`, `QuestionContainer` | **`specification only`** | [`specs/k8s/spacefractions-deployment.yaml`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/k8s/spacefractions-deployment.yaml) | Docker container images (`spacefractions:latest`) specified in Kubernetes manifest. |
| **11** | Physical View | `ContainerDiagram` | `UserContainer` | **`implemented`** | [`space_fractions/`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/) | Standalone offline client bundle. |

---

## 2. Traceability to Requirements (`Architecture_Documentation.md`)

| Requirement ID | Type | Requirement Summary | Implementation Status | Implementation Evidence |
| :---: | :---: | :--- | :---: | :--- |
| **FR-1** | Functional | Play game: users play the interactive fraction game | **`implemented`** | [`space_fractions/index.html`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/index.html)<br>[`space_fractions/game.js`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/space_fractions/game.js) |
| **NFR-1** | Non-Functional | Performance: game responds quickly to user input | **`implemented`** | Local client DOM execution without network latency; procedural Web Audio API and synchronous DOM rendering. |
| **ASR-1** | Arch. Significant | Data durability: question data persisted and recoverable | **`specification only`** | Standalone client uses deterministic seeded PRNG for reproducible question sets; enterprise database persistence is specification-only ([`specs/sql/game_ddl.sql`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/sql/game_ddl.sql)). |
| **ASR-2** | Arch. Significant | Security: authentication and secure access | **`specification only`** | OAuth2 and HashiCorp Vault specified in documentation Section F; not required for offline desktop client. |
| **A1** | Assumption | 1000 concurrent users | **`specification only`** | Cloud scalability assumption addressed by Kubernetes 3-replica specification in [`specs/k8s/`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/specs/k8s/spacefractions-deployment.yaml). |
| **A2** | Assumption | 1 hour per session | **`implemented`** | Standalone local game supports unlimited session durations with pause/resume capability. |

---

## 3. Explicit Architecture Omissions (Honesty Statement)

The following cloud technologies appear in `Architecture_Documentation.md` but are **explicitly NOT deployed** in the standalone local game:
1. **Kubernetes Cluster & Ingress:** Not running locally. Preserved as specification in `specs/k8s/spacefractions-deployment.yaml`.
2. **PostgreSQL & Redis:** No database or cache server is installed or required. Game state resides in-memory in the client. Schema is preserved in `specs/sql/game_ddl.sql`.
3. **RabbitMQ Messaging:** No message broker is deployed; all game events are synchronous in-process DOM events.
4. **OAuth2 / Vault Authentication:** The local game requires no login or cloud credentials.
5. **Node.js/Express Backend Server:** The game runs as an offline HTML5/JavaScript application directly on the client, eliminating server dependencies.
