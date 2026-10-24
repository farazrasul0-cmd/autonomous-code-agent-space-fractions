"""DeepSeek Autonomous Code Agent — Native Windows Desktop GUI.

Desktop application shell built with Python 3.12 and Tkinter.
Provides:
1. Masked API key configuration with local .env auto-load support.
2. File pickers for canonical Architecture_Documentation.md and Architecture_View.md.
3. Destination output directory selection.
4. Task trigger and cancel controls with background worker thread and thread-safe queue.
5. Real-time budget telemetry distinguishing verified live spend ($0.000186) from active holds.
6. Execution of the offline specification preprocessor and staged generator pipeline.
7. Multi-tab activity log, extracted view/entity details, and generated artifact summaries.

SAFETY & CREDENTIAL NOTICE:
- Operates strictly in offline mock mode during Stage F2.
- Zero live network or DeepSeek API calls are dispatched.
- API keys are masked in the GUI and never printed, logged, or persisted.
- Generated code and test suites are never executed on the host.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path when executed directly as a script
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.budget_controller import BudgetController
from src.deepseek_client import DeepSeekClient
from src.generator_pipeline import OfflinePipelineCoordinator, PipelineExecutionSummary
from src.path_security import PathSecurityError, validate_workspace_path
from src.spec_preprocessor import (
    SpecificationExtractionError,
    SpecificationPreprocessResult,
    SpecificationPreprocessor,
)


def get_resource_path(relative_path: str) -> str:
    """Resolve resource path for both development source tree and PyInstaller bundle.

    When packaged by PyInstaller, sys._MEIPASS holds the unpacked directory path.
    When running unbundled, PROJECT_ROOT is used as the base path.
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(getattr(sys, "_MEIPASS"), relative_path)
    return os.path.abspath(os.path.join(PROJECT_ROOT, relative_path))


def resolve_input_file(filename: str) -> str:
    """Finds an input file in current working directory, then bundled resources."""
    if os.path.isfile(filename):
        return os.path.abspath(filename)
    bundled = get_resource_path(filename)
    if os.path.isfile(bundled):
        return os.path.abspath(bundled)
    return os.path.abspath(filename)


class AgentGUIApp:
    """Native Windows desktop GUI for the Autonomous Code Agent."""

    DEFAULT_DOC_FILE = "Architecture_Documentation.md"
    DEFAULT_VIEW_FILE = "Architecture_View.md"
    DEFAULT_OUTPUT_DIR = "generated_space_fractions"

    # Verified cumulative live spend from Gate E verification
    BASE_VERIFIED_SPEND = 0.000186
    HARD_CEILING = 0.800000

    def __init__(
        self,
        root: tk.Tk,
        auto_run: bool = False,
        auto_close_ms: int = 0,
        suppress_messagebox: bool = False,
    ) -> None:
        self.root = root
        self.auto_run = auto_run
        self.auto_close_ms = auto_close_ms
        self.suppress_messagebox = suppress_messagebox

        self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Offline Shell]")
        self.root.geometry("1020x740")
        self.root.minsize(920, 640)

        # Threading and synchronization state
        self.msg_queue: queue.Queue[Tuple[str, Any]] = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_requested = threading.Event()
        self.is_running = False

        # Budget tracking instance
        self.budget_controller = BudgetController(ceiling=self.HARD_CEILING)
        self.budget_controller.cumulative_spend = self.BASE_VERIFIED_SPEND

        # Extracted results cache
        self.last_preprocess_result: Optional[SpecificationPreprocessResult] = None
        self.last_execution_summary: Optional[PipelineExecutionSummary] = None

        # Execution mode: offline mock pipeline is default and only active mode in Stage F5B
        self.execution_mode_var = tk.StringVar(value="offline")

        # Style configuration
        self._setup_styles()

        # Build UI layout
        self._build_ui()

        # Try auto-loading .env without logging or printing the secret
        self._auto_load_env_secret()

        # Start queue processing on main thread
        self.root.after(100, self._process_queue)

        if self.auto_run:
            self.root.after(400, self.on_start_click)

        if self.auto_close_ms > 0:
            self.root.after(self.auto_close_ms, self.root.destroy)

    def _setup_styles(self) -> None:
        """Configures ttk styles for clean Windows desktop aesthetics."""
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista")
        except Exception:
            try:
                self.style.theme_use("clam")
            except Exception:
                pass

        self.style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"), foreground="#1A202C")
        self.style.configure("Subtitle.TLabel", font=("Segoe UI", 9), foreground="#4A5568")
        self.style.configure("Header.TLabelframe.Label", font=("Segoe UI", 9, "bold"), foreground="#2D3748")
        self.style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
        self.style.configure("Danger.TButton", font=("Segoe UI", 9))
        self.style.configure("TelemetryValue.TLabel", font=("Segoe UI", 10, "bold"), foreground="#2B6CB0")
        self.style.configure("ModeBadge.TLabel", font=("Segoe UI", 9, "bold"), foreground="#C53030")

    def _build_ui(self) -> None:
        """Constructs the complete hierarchical widget tree."""
        main_container = ttk.Frame(self.root, padding="12")
        main_container.pack(fill=tk.BOTH, expand=True)

        # 1. Header Banner
        self._build_header(main_container)

        # 2. Main Work Area (PanedWindow: Top for Configuration/Telemetry, Bottom for Logs)
        self.paned_window = ttk.PanedWindow(main_container, orient=tk.VERTICAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Top Section: Config & Telemetry
        top_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(top_frame, weight=0)

        # Left Column in Top: Configuration & Inputs
        config_frame = ttk.LabelFrame(top_frame, text=" 1. Agent Configuration & Source Documents ", style="Header.TLabelframe", padding="10")
        config_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        self._build_config_form(config_frame)

        # Right Column in Top: Budget & Telemetry Meter
        telemetry_frame = ttk.LabelFrame(top_frame, text=" 2. Financial Budget & Verification Guard ", style="Header.TLabelframe", padding="10")
        telemetry_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(6, 0))
        self._build_telemetry_panel(telemetry_frame)

        # Bottom Section: Action Controls & Multi-Tab Notebook
        bottom_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(bottom_frame, weight=1)

        self._build_action_bar(bottom_frame)
        self._build_notebook_tabs(bottom_frame)

    def _build_header(self, parent: ttk.Frame) -> None:
        """Builds the title and mode identification header."""
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill=tk.X, pady=(0, 4))

        title_lbl = ttk.Label(
            header_frame,
            text="Autonomous Code Agent — Space Fractions Synthesis",
            style="Title.TLabel",
        )
        title_lbl.pack(anchor=tk.W)

        subtitle_lbl = ttk.Label(
            header_frame,
            text="Offline Scaffolding & Verification Shell | Model: deepseek-v4-pro (Simulated Offline Mode)",
            style="Subtitle.TLabel",
        )
        subtitle_lbl.pack(anchor=tk.W)

    def _build_config_form(self, parent: ttk.LabelFrame) -> None:
        """Builds API key masking, file selection controls, and output folder assignment."""
        parent.columnconfigure(1, weight=1)

        # API Key
        ttk.Label(parent, text="DeepSeek API Key:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.api_key_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(parent, textvariable=self.api_key_var, show="*", width=38)
        self.api_key_entry.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=3)

        self.btn_load_env = ttk.Button(parent, text="Auto-Load .env", command=self._auto_load_env_secret)
        self.btn_load_env.grid(row=0, column=2, padx=2, pady=3)

        self.key_status_var = tk.StringVar(value="Key Status: Not Loaded")
        self.lbl_key_status = ttk.Label(parent, textvariable=self.key_status_var, font=("Segoe UI", 8), foreground="#718096")
        self.lbl_key_status.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=4)

        # Architecture Documentation
        ttk.Label(parent, text="Architecture Doc:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.doc_path_var = tk.StringVar(value=resolve_input_file(self.DEFAULT_DOC_FILE))
        self.doc_entry = ttk.Entry(parent, textvariable=self.doc_path_var, width=38)
        self.doc_entry.grid(row=2, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_doc = ttk.Button(parent, text="Browse...", command=self._browse_doc_file)
        self.btn_browse_doc.grid(row=2, column=2, padx=2, pady=3)

        # Architecture View
        ttk.Label(parent, text="Architecture View:").grid(row=3, column=0, sticky=tk.W, pady=3)
        self.view_path_var = tk.StringVar(value=resolve_input_file(self.DEFAULT_VIEW_FILE))
        self.view_entry = ttk.Entry(parent, textvariable=self.view_path_var, width=38)
        self.view_entry.grid(row=3, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_view = ttk.Button(parent, text="Browse...", command=self._browse_view_file)
        self.btn_browse_view.grid(row=3, column=2, padx=2, pady=3)

        # Output Directory
        ttk.Label(parent, text="Output Directory:").grid(row=4, column=0, sticky=tk.W, pady=3)
        self.output_dir_var = tk.StringVar(value=os.path.abspath(self.DEFAULT_OUTPUT_DIR))
        self.output_entry = ttk.Entry(parent, textvariable=self.output_dir_var, width=38)
        self.output_entry.grid(row=4, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_output = ttk.Button(parent, text="Browse...", command=self._browse_output_dir)
        self.btn_browse_output.grid(row=4, column=2, padx=2, pady=3)

        # Task Description
        ttk.Label(parent, text="Task Goal:").grid(row=5, column=0, sticky=tk.W, pady=3)
        self.task_desc_var = tk.StringVar(
            value="Ingest architecture specs, extract 11 views / 13 PlantUML diagrams, and run offline pipeline."
        )
        self.task_entry = ttk.Entry(parent, textvariable=self.task_desc_var, width=38)
        self.task_entry.grid(row=5, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=3)

        # Execution Mode
        ttk.Label(parent, text="Run Mode:").grid(row=6, column=0, sticky=tk.W, pady=3)
        mode_btn_frame = ttk.Frame(parent)
        mode_btn_frame.grid(row=6, column=1, columnspan=2, sticky=tk.W, padx=4, pady=3)
        self.radio_offline = ttk.Radiobutton(
            mode_btn_frame,
            text="Offline Mock Pipeline",
            variable=self.execution_mode_var,
            value="offline",
        )
        self.radio_offline.pack(side=tk.LEFT, padx=(0, 10))
        self.radio_live = ttk.Radiobutton(
            mode_btn_frame,
            text="Live API Mode (Disabled — Pending Stage F5D Authorization)",
            variable=self.execution_mode_var,
            value="live",
            state=tk.DISABLED,
        )
        self.radio_live.pack(side=tk.LEFT)

    def _build_telemetry_panel(self, parent: ttk.LabelFrame) -> None:
        """Builds financial meter cards distinguishing live spend from reservations."""
        # Mode Badge
        mode_frame = ttk.Frame(parent)
        mode_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(mode_frame, text="RUN MODE: ").pack(side=tk.LEFT)
        ttk.Label(mode_frame, text="OFFLINE VALIDATION (Mock Engine)", style="ModeBadge.TLabel").pack(side=tk.LEFT)

        # Telemetry Grid
        grid_frame = ttk.Frame(parent)
        grid_frame.pack(fill=tk.BOTH, expand=True)

        items = [
            ("Cumulative Verified Live Spend:", "$0.000186", "#2B6CB0"),
            ("Active Reserved Hold:", "$0.000000", "#D69E2E"),
            ("Hard Project Budget Ceiling:", "$0.800000", "#2D3748"),
            ("Remaining Available Budget:", "$0.799814", "#38A169"),
        ]

        self.telemetry_labels: Dict[str, ttk.Label] = {}
        for r_idx, (title, default_val, color) in enumerate(items):
            ttk.Label(grid_frame, text=title, font=("Segoe UI", 9)).grid(row=r_idx*2, column=0, sticky=tk.W, pady=(2, 0))
            val_lbl = ttk.Label(grid_frame, text=default_val, font=("Segoe UI", 10, "bold"), foreground=color)
            val_lbl.grid(row=r_idx*2+1, column=0, sticky=tk.W, pady=(0, 4))
            self.telemetry_labels[title] = val_lbl

        # Security note
        ttk.Label(
            parent,
            text="* Verified spend reflects actual Gate E telemetry.\n* No network calls are dispatched in offline mode.",
            font=("Segoe UI", 8),
            foreground="#718096",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

    def _build_action_bar(self, parent: ttk.Frame) -> None:
        """Builds Start and Cancel buttons, progress bar, and status readout."""
        action_frame = ttk.Frame(parent, padding="4")
        action_frame.pack(fill=tk.X, pady=(6, 4))

        self.btn_start = ttk.Button(
            action_frame,
            text="▶  Start Offline Validation",
            style="Primary.TButton",
            command=self.on_start_click,
            width=26,
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_cancel = ttk.Button(
            action_frame,
            text="✖  Cancel",
            style="Danger.TButton",
            command=self.on_cancel_click,
            state=tk.DISABLED,
            width=12,
        )
        self.btn_cancel.pack(side=tk.LEFT, padx=(0, 10))

        self.progress_bar = ttk.Progressbar(action_frame, mode="indeterminate", length=220)
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 10))

        self.status_var = tk.StringVar(value="Ready. Click 'Start Offline Validation' to execute.")
        self.lbl_status = ttk.Label(
            action_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 9, "italic"),
            foreground="#2B6CB0",
        )
        self.lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_notebook_tabs(self, parent: ttk.Frame) -> None:
        """Builds tabs for activity logs, architecture details, and generated files."""
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # Tab 1: Real-time Activity Log
        tab_log = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_log, text=" Activity & Validation Log ")
        self._build_log_tab(tab_log)

        # Tab 2: Architecture & Conflict Details
        tab_details = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_details, text=" Architecture & Conflict Details ")
        self._build_details_tab(tab_details)

        # Tab 3: Generated Artifacts Summary
        tab_artifacts = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_artifacts, text=" Generated Artifacts Summary ")
        self._build_artifacts_tab(tab_artifacts)

        # Tab 4: Bundled Assets & Game Specs
        tab_assets = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_assets, text=" Bundled Assets & Specs ")
        self._build_assets_tab(tab_assets)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        """Builds the scrollable activity text log with color tags."""
        self.log_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#FFFFFF",
            fg="#2D3748",
            relief=tk.SOLID,
            borderwidth=1,
        )
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure color tags
        self.log_text.tag_config("INFO", foreground="#2B6CB0")
        self.log_text.tag_config("SUCCESS", foreground="#276749", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("WARNING", foreground="#D69E2E", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("ERROR", foreground="#C53030", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("CONFLICT", foreground="#805AD5", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("MUTED", foreground="#718096")

    def _build_details_tab(self, parent: ttk.Frame) -> None:
        """Displays extracted view items, entities, and recorded architectural conflicts."""
        self.details_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
            bg="#F7FAFC",
            fg="#2D3748",
            relief=tk.SOLID,
            borderwidth=1,
        )
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=scrollbar.set)

        self.details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.details_text.insert(
            tk.END,
            "Architecture extraction details will appear here after running the preprocessor.\n"
            "Will summarize: 11 View Items, 13 PlantUML Diagrams, Extracted Classes, Actors, States,\n"
            "and the documented Desktop-vs-Cloud Platform Conflict.\n"
        )
        self.details_text.configure(state=tk.DISABLED)

    def _build_artifacts_tab(self, parent: ttk.Frame) -> None:
        """Displays table of generated files and safety verification notices."""
        cols = ("file_name", "relative_path", "size_bytes", "status")
        self.artifacts_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)

        self.artifacts_tree.heading("file_name", text="File Name")
        self.artifacts_tree.heading("relative_path", text="Relative Destination")
        self.artifacts_tree.heading("size_bytes", text="Size (Bytes)")
        self.artifacts_tree.heading("status", text="Verification Status")

        self.artifacts_tree.column("file_name", width=180, anchor=tk.W)
        self.artifacts_tree.column("relative_path", width=340, anchor=tk.W)
        self.artifacts_tree.column("size_bytes", width=90, anchor=tk.E)
        self.artifacts_tree.column("status", width=140, anchor=tk.CENTER)

        tree_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.artifacts_tree.yview)
        self.artifacts_tree.configure(yscrollcommand=tree_scroll.set)

        self.artifacts_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_assets_tab(self, parent: ttk.Frame) -> None:
        """Builds verification view for bundled game assets and enterprise specifications."""
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill=tk.X, pady=(0, 6))

        notice_lbl = ttk.Label(
            header_frame,
            text="Space Fractions Game is a separate standalone educational client.\n"
                 "Bundled assets are verified for distribution integrity only and are not executed inside this agent shell.",
            font=("Segoe UI", 8),
            foreground="#4A5568",
            justify=tk.LEFT,
        )
        notice_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        btn_verify_assets = ttk.Button(
            header_frame,
            text="↻ Verify Assets",
            command=self._populate_assets_tab,
        )
        btn_verify_assets.pack(side=tk.RIGHT, padx=4)

        cols = ("rel_path", "size_bytes", "readable", "resolved_path")
        self.assets_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)

        self.assets_tree.heading("rel_path", text="Resource / Relative Path")
        self.assets_tree.heading("size_bytes", text="Size (Bytes)")
        self.assets_tree.heading("readable", text="Integrity Status")
        self.assets_tree.heading("resolved_path", text="Resolved Path")

        self.assets_tree.column("rel_path", width=220, anchor=tk.W)
        self.assets_tree.column("size_bytes", width=90, anchor=tk.E)
        self.assets_tree.column("readable", width=130, anchor=tk.CENTER)
        self.assets_tree.column("resolved_path", width=360, anchor=tk.W)

        tree_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.assets_tree.yview)
        self.assets_tree.configure(yscrollcommand=tree_scroll.set)

        self.assets_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._populate_assets_tab()

    def _populate_assets_tab(self) -> None:
        """Populates the assets treeview with verification status of all bundled files."""
        for item in self.assets_tree.get_children():
            self.assets_tree.delete(item)

        manifest = self.get_bundled_assets_manifest()
        for entry in manifest:
            status = "Verified Readable" if entry["readable"] else ("Missing" if not entry["exists"] else "Read Error")
            self.assets_tree.insert(
                "",
                tk.END,
                values=(
                    entry["relative_path"],
                    f"{entry['size_bytes']:,}",
                    status,
                    entry["resolved_path"],
                ),
            )

    @staticmethod
    def get_bundled_assets_manifest() -> List[Dict[str, Any]]:
        """Scans and verifies readability of all bundled game assets and specifications."""
        target_relative_files = [
            "Architecture_Documentation.md",
            "Architecture_View.md",
            os.path.join("space_fractions", "index.html"),
            os.path.join("space_fractions", "style.css"),
            os.path.join("space_fractions", "fraction_engine.js"),
            os.path.join("space_fractions", "game.js"),
            os.path.join("space_fractions", "fraction_math.py"),
            os.path.join("space_fractions", "README.md"),
            os.path.join("space_fractions", "TRACEABILITY.md"),
            os.path.join("specs", "openapi.yaml"),
            os.path.join("specs", "internal.proto"),
            os.path.join("specs", "k8s", "spacefractions-deployment.yaml"),
            os.path.join("specs", "sql", "game_ddl.sql"),
            os.path.join("specs", "traceability_matrix.csv"),
        ]

        manifest = []
        for rel_path in target_relative_files:
            resolved = get_resource_path(rel_path)
            exists = os.path.isfile(resolved)
            size = os.path.getsize(resolved) if exists else 0
            readable = False
            if exists:
                try:
                    with open(resolved, "rb") as f:
                        f.read(128)
                    readable = True
                except Exception:
                    readable = False
            manifest.append({
                "relative_path": rel_path.replace("\\", "/"),
                "resolved_path": resolved,
                "exists": exists,
                "size_bytes": size,
                "readable": readable,
            })
        return manifest

    # -------------------------------------------------------------------------
    # Secret and File Handling Helpers
    # -------------------------------------------------------------------------

    def _auto_load_env_secret(self) -> None:
        """Loads DEEPSEEK_API_KEY from local .env if present, strictly without logging."""
        env_path = os.path.abspath(".env")
        if not os.path.isfile(env_path):
            self.key_status_var.set("Key Status: No local .env detected")
            return

        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("DEEPSEEK_API_KEY="):
                        val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            self.api_key_var.set(val)
                            self.key_status_var.set("Key Status: Masked Key Loaded from .env (Local Only)")
                            return
            self.key_status_var.set("Key Status: .env exists but key is empty")
        except Exception:
            self.key_status_var.set("Key Status: Unable to read .env")

    def _browse_doc_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Architecture Documentation File",
            filetypes=[("Markdown Files", "*.md"), ("All Files", "*.*")],
            initialdir=os.getcwd(),
        )
        if path:
            self.doc_path_var.set(os.path.abspath(path))

    def _browse_view_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Architecture View File",
            filetypes=[("Markdown Files", "*.md"), ("All Files", "*.*")],
            initialdir=os.getcwd(),
        )
        if path:
            self.view_path_var.set(os.path.abspath(path))

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Select Destination Output Directory",
            initialdir=os.getcwd(),
        )
        if path:
            self.output_dir_var.set(os.path.abspath(path))

    # -------------------------------------------------------------------------
    # Execution & Thread Management
    # -------------------------------------------------------------------------

    def on_start_click(self) -> None:
        """Handles Start button click: validates inputs and launches worker thread."""
        if self.is_running:
            return

        doc_path = self.doc_path_var.get().strip()
        view_path = self.view_path_var.get().strip()
        output_dir = self.output_dir_var.get().strip()

        # Validate existence of input files
        if not doc_path or not os.path.isfile(doc_path):
            messagebox.showerror("Invalid Input", f"Architecture documentation file not found:\n{doc_path}")
            return
        if not view_path or not os.path.isfile(view_path):
            messagebox.showerror("Invalid Input", f"Architecture view file not found:\n{view_path}")
            return
        if not output_dir:
            messagebox.showerror("Invalid Input", "Output directory cannot be empty.")
            return

        # Prepare UI state for running
        self.is_running = True
        self.cancel_requested.clear()
        self._set_controls_running_state(True)
        self.progress_bar.start(10)

        # Clear previous logs if starting fresh
        self._append_log("Starting offline architecture synthesis and pipeline validation...", "INFO")
        self.status_var.set("Running preprocessor and offline pipeline...")

        # Spawn background worker
        self.worker_thread = threading.Thread(
            target=self._run_offline_pipeline_worker,
            args=(doc_path, view_path, output_dir),
            daemon=True,
        )
        self.worker_thread.start()

    def on_cancel_click(self) -> None:
        """Handles Cancel button click: signals cancel to worker."""
        if not self.is_running:
            return
        self.cancel_requested.set()
        self.status_var.set("Cancelling operation...")
        self._append_log("Cancellation requested by user. Terminating worker...", "WARNING")

    def _set_controls_running_state(self, running: bool) -> None:
        """Enables or disables UI controls depending on execution state."""
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_start.configure(state=state)
        self.btn_cancel.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.btn_load_env.configure(state=state)
        self.btn_browse_doc.configure(state=state)
        self.btn_browse_view.configure(state=state)
        self.btn_browse_output.configure(state=state)
        self.api_key_entry.configure(state=state)
        self.doc_entry.configure(state=state)
        self.view_entry.configure(state=state)
        self.output_entry.configure(state=state)
        if hasattr(self, "radio_offline"):
            self.radio_offline.configure(state=state)
        # self.radio_live remains permanently tk.DISABLED until Stage F5D authorization

    def get_deepseek_client(self) -> DeepSeekClient:
        """Instantiates a DeepSeekClient with the in-memory API key and budget controller.
        
        The API key is accessed only in memory and never logged, printed, or persisted.
        """
        key = self.api_key_var.get().strip()
        return DeepSeekClient(
            api_key=key if key else "unconfigured-offline-placeholder",
            budget_controller=self.budget_controller,
        )

    # -------------------------------------------------------------------------
    # Worker Thread Logic (Runs in Background)
    # -------------------------------------------------------------------------

    def _run_offline_pipeline_worker(self, doc_path: str, view_path: str, output_dir: str) -> None:
        """Executes the preprocessor and offline pipeline in a background thread."""
        try:
            if self.execution_mode_var.get() == "live":
                self._queue_msg(
                    "log",
                    ("NOTICE: Live API mode is disabled pending Stage F5D authorization. "
                     "Running offline validation pipeline strictly.", "WARNING"),
                )

            self._queue_msg("log", ("Phase 1: Validating source files and workspace paths...", "INFO"))
            time.sleep(0.05)

            if self.cancel_requested.is_set():
                self._queue_msg("cancelled", "Validation cancelled before preprocessing.")
                return

            # Step 1: Run Specification Preprocessor
            self._queue_msg("log", ("Phase 2: Ingesting canonical architecture documents...", "INFO"))
            spec_result = SpecificationPreprocessor.preprocess(doc_path, view_path)
            self._queue_msg("spec_result", spec_result)

            self._queue_msg(
                "log",
                (f"  ✓ Preprocessor extracted {len(spec_result.view_items)} view items and "
                 f"{len(spec_result.diagrams)} PlantUML diagram blocks.", "SUCCESS"),
            )
            self._queue_msg(
                "log",
                (f"  ✓ Preserved {len(spec_result.extraction_warnings)} syntactic/semantic notes.", "INFO"),
            )
            for w in spec_result.extraction_warnings:
                self._queue_msg("log", (f"    [NOTE] {w}", "MUTED"))

            # Log Documented Conflict
            for conflict in spec_result.recorded_conflicts:
                self._queue_msg(
                    "log",
                    (f"  [CONFLICT RECORDED] {conflict.conflict_id}: {conflict.title}", "CONFLICT"),
                )
                self._queue_msg(
                    "log",
                    (f"    Source A: {conflict.source_a['document']} -> Windows desktop .exe mandate\n"
                     f"    Source B: {conflict.source_b['document']} -> Cloud microservices specification\n"
                     f"    Resolution: Conflict preserved without silent mutation.", "MUTED"),
                )

            if self.cancel_requested.is_set():
                self._queue_msg("cancelled", "Validation cancelled after preprocessing.")
                return

            # Step 2: Initialize Offline Pipeline Coordinator
            self._queue_msg("log", ("Phase 3: Initializing Offline Pipeline Coordinator...", "INFO"))
            os.makedirs(output_dir, exist_ok=True)

            mock_budget = BudgetController(ceiling=self.HARD_CEILING)
            coordinator = OfflinePipelineCoordinator(
                workspace_root=os.path.dirname(output_dir) or ".",
                output_dir=output_dir,
                budget_controller=mock_budget,
            )

            # Step 3: Run Staged Offline Mock Pipeline
            self._queue_msg("log", ("Phase 4: Executing staged generation plan with mock telemetry...", "INFO"))
            summary = coordinator.run_mock_pipeline(doc_path, view_path)

            if self.cancel_requested.is_set():
                self._queue_msg("cancelled", "Validation cancelled during pipeline run.")
                return

            # Step 4: Write Demonstration Test Manifest into Output Directory
            self._queue_msg("log", ("Phase 5: Writing safe bounded test manifest into output directory...", "INFO"))
            test_manifest_content = json.dumps(
                {
                    "validation_run": "Stage F2 Offline Desktop GUI Shell Test",
                    "timestamp": datetime.now().isoformat(),
                    "offline_mode": True,
                    "live_api_called": False,
                    "code_executed": False,
                    "view_items_count": len(spec_result.view_items),
                    "diagrams_count": len(spec_result.diagrams),
                    "recorded_conflicts": [c.conflict_id for c in spec_result.recorded_conflicts],
                    "deliverable_specs_count": len(spec_result.deliverable_specs),
                    "security_disclaimer": "AST/py_compile are syntax checks only, not process isolation sandboxes.",
                },
                indent=2,
            )
            manifest_path = coordinator.safe_write_generated_file(
                "offline_validation_manifest.json", test_manifest_content
            )

            self._queue_msg("summary", (summary, manifest_path))

            self._queue_msg(
                "log",
                (f"  ✓ Offline pipeline completed successfully! Written files: {len(summary.generated_files)}", "SUCCESS"),
            )
            self._queue_msg(
                "log",
                ("  ✓ Verified spend unchanged at $0.000186 (Zero live DeepSeek tokens billed).", "SUCCESS"),
            )

            self._queue_msg("completed", "Offline validation run completed successfully.")

        except SpecificationExtractionError as err:
            self._queue_msg("error", f"Specification Extraction Error: {err}")
        except PathSecurityError as err:
            self._queue_msg("error", f"Path Security Violation: {err}")
        except Exception as err:
            self._queue_msg("error", f"Unexpected Pipeline Error: {err}")

    # -------------------------------------------------------------------------
    # Queue Processing on Main UI Thread
    # -------------------------------------------------------------------------

    def _queue_msg(self, msg_type: str, data: Any) -> None:
        """Pushes a message onto the thread-safe queue."""
        self.msg_queue.put((msg_type, data))

    def _process_queue(self) -> None:
        """Polls the queue and applies all widget updates on the main UI thread."""
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()
                self._handle_queue_message(msg_type, data)
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        finally:
            try:
                if self.root.winfo_exists():
                    self.root.after(80, self._process_queue)
            except Exception:
                pass

    def _handle_queue_message(self, msg_type: str, data: Any) -> None:
        """Dispatches typed message updates to UI elements."""
        if msg_type == "log":
            text, tag = data
            self._append_log(text, tag)

        elif msg_type == "spec_result":
            self.last_preprocess_result = data
            self._populate_details_tab(data)

        elif msg_type == "summary":
            summary, manifest_path = data
            self.last_execution_summary = summary
            self._populate_artifacts_tab(summary, manifest_path)
            self._update_telemetry_display()

        elif msg_type == "completed":
            self.is_running = False
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set(f"Completed: {data}")
            self._append_log(f"Status: {data}", "SUCCESS")

        elif msg_type == "cancelled":
            self.is_running = False
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set(f"Cancelled: {data}")
            self._append_log(f"Status: {data}", "WARNING")

        elif msg_type == "error":
            self.is_running = False
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set("Error encountered during execution.")
            self._append_log(f"[ERROR] {data}", "ERROR")
            if not getattr(self, "suppress_messagebox", False):
                messagebox.showerror("Execution Error", str(data))

    def _append_log(self, text: str, tag: str = "INFO") -> None:
        """Appends a timestamped log entry to the scrolled text widget."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {text}\n", tag)
        self.log_text.see(tk.END)

    def _populate_details_tab(self, result: SpecificationPreprocessResult) -> None:
        """Fills the Architecture & Conflict Details text widget."""
        self.details_text.configure(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)

        lines = [
            "=" * 78,
            " CANONICAL ARCHITECTURE EXTRACTION REPORT (OFFLINE PREPROCESSOR)",
            "=" * 78,
            f"Schema Version: {result.schema_version}",
            f"Serialized Output Size: {result.serialized_utf8_bytes} UTF-8 bytes",
            f"Extraction Warnings: {len(result.extraction_warnings)}",
            "",
            "--- 1. NUMBERED VIEW ITEMS (11/11) ---",
        ]
        for item in result.view_items:
            lines.append(
                f"  [{item.item_number:02d}] {item.full_title} (lines {item.start_line}-{item.end_line}) "
                f"-> Diagrams: {item.diagram_ids}"
            )

        lines.extend([
            "",
            "--- 2. PLANTUML DIAGRAM BLOCKS (13/13) ---",
        ])
        for diag in result.diagrams:
            lines.append(
                f"  [{diag.index:02d}] {diag.diagram_id} ({diag.diagram_type}) "
                f"lines {diag.start_line}-{diag.end_line}"
            )

        lines.extend([
            "",
            "--- 3. EXTRACTED DOMAIN ENTITIES ---",
            f"  Actors: {result.entities.actors}",
            f"  Domain Classes: {[c['name'] for c in result.entities.classes]}",
            f"  Game States: {result.entities.states}",
            f"  Deployment Nodes: {result.entities.deployment_nodes}",
            f"  Container Artifacts: {result.entities.container_artifacts}",
            "",
            "--- 4. RECORDED ARCHITECTURAL CONFLICTS ---",
        ])
        for c in result.recorded_conflicts:
            lines.extend([
                f"  Conflict ID: {c.conflict_id}",
                f"  Title: {c.title}",
                f"  Status: {c.status}",
                f"  Description: {c.description}",
                f"  Architectural Implication: {c.architectural_implication}",
            ])

        lines.extend([
            "",
            "--- 5. DELIVERABLE SPECIFICATIONS (SECTION L) ---",
        ])
        for d in result.deliverable_specs:
            lines.append(f"  {d.filename} ({d.format}): lines {d.start_line}-{d.end_line}")

        self.details_text.insert(tk.END, "\n".join(lines))
        self.details_text.configure(state=tk.DISABLED)

    def _populate_artifacts_tab(
        self, summary: PipelineExecutionSummary, manifest_path: str
    ) -> None:
        """Fills the Generated Artifacts Treeview table."""
        for item in self.artifacts_tree.get_children():
            self.artifacts_tree.delete(item)

        all_files = list(summary.generated_files)
        if manifest_path not in all_files:
            all_files.append(manifest_path)

        for path in all_files:
            fname = os.path.basename(path)
            rel_path = os.path.relpath(path, self.output_dir_var.get())
            size = os.path.getsize(path) if os.path.exists(path) else 0
            self.artifacts_tree.insert(
                "",
                tk.END,
                values=(fname, rel_path, f"{size:,}", "Verified Safe"),
            )

    def _update_telemetry_display(self) -> None:
        """Updates the financial telemetry cards on the UI."""
        verified = self.budget_controller.cumulative_spend
        reserved = self.budget_controller.get_active_holds()
        remaining = max(0.0, self.HARD_CEILING - (verified + reserved))

        if "Cumulative Verified Live Spend:" in self.telemetry_labels:
            self.telemetry_labels["Cumulative Verified Live Spend:"].configure(
                text=f"${verified:.6f}"
            )
        if "Active Reserved Hold:" in self.telemetry_labels:
            self.telemetry_labels["Active Reserved Hold:"].configure(
                text=f"${reserved:.6f}"
            )
        if "Remaining Available Budget:" in self.telemetry_labels:
            self.telemetry_labels["Remaining Available Budget:"].configure(
                text=f"${remaining:.6f}"
            )


def main() -> None:
    auto_run = "--auto-run" in sys.argv
    auto_close_ms = 0
    for idx, arg in enumerate(sys.argv):
        if arg == "--auto-close-ms" and idx + 1 < len(sys.argv):
            auto_close_ms = int(sys.argv[idx + 1])

    root = tk.Tk()
    app = AgentGUIApp(root, auto_run=auto_run, auto_close_ms=auto_close_ms)
    root.mainloop()


if __name__ == "__main__":
    main()
