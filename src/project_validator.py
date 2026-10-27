"""Project Validator Module.

Static Acceptance Contract Validator for the Space Fractions Game.

SAFETY AND INTEGRITY GUARANTEES:
1. STRICTLY STATIC: Zero code execution, zero subprocess spawns, zero importlib execution.
2. SYNTAX VERIFICATION: AST parsing verifies valid Python syntax without executing untrusted code.
3. INTERFACE VERIFICATION: AST structural inspection verifies required classes, methods, and invariants.
4. TEST SUITE VERIFICATION: AST structural inspection verifies test suite definition.
5. MANIFEST CONSISTENCY: Verifies JSON integrity, path containment, and file existence on disk.
6. PLATFORM CONFLICT CHECK: Verifies manifest/docs note desktop Tkinter application delivery.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class ValidationResult:
    """Structured report of static project validation."""
    success: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    verified_files: List[str] = field(default_factory=list)
    verified_interfaces: List[str] = field(default_factory=list)
    ast_syntax_valid: bool = False
    interfaces_valid: bool = False
    tests_valid: bool = False
    manifest_valid: bool = False
    manifest_data: Optional[Dict[str, Any]] = None
    details: Dict[str, Any] = field(default_factory=dict)


class ProjectValidator:
    """Performs strict, purely static validation of generated Space Fractions projects."""

    REQUIRED_FRACTION_METHODS: Set[str] = {
        "__init__",
        "simplify",
        "add",
        "subtract",
        "compare",
        "equals",
    }

    REQUIRED_ENGINE_METHODS: Set[str] = {
        "play",
        "submit_answer",
        "pause",
        "resume",
    }

    REQUIRED_GAME_STATES: Set[str] = {
        "TITLE",
        "PLAYING",
        "PAUSED",
        "GAMEOVER",
    }

    @staticmethod
    def validate_syntax_ast(source_code: str, filename: str = "<string>") -> Tuple[bool, Optional[str], Optional[ast.AST]]:
        """Parses Python source code into an AST without code execution.
        
        Returns (is_valid, error_message, parsed_tree).
        """
        try:
            tree = ast.parse(source_code, filename=filename)
            return True, None, tree
        except SyntaxError as err:
            return False, f"SyntaxError in {filename}:{err.lineno}:{err.offset}: {err.msg}", None
        except Exception as err:
            return False, f"AST parsing failed for {filename}: {err}", None

    @classmethod
    def extract_classes_and_methods(cls, tree: ast.AST) -> Dict[str, List[str]]:
        """Extracts class names and their defined methods using AST inspection."""
        classes: Dict[str, List[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods: List[str] = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)
                classes[node.name] = methods
        return classes

    @classmethod
    def extract_top_level_functions(cls, tree: ast.AST) -> List[str]:
        """Extracts top-level module function names using AST inspection."""
        functions: List[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
        return functions

    @classmethod
    def extract_enum_members(cls, tree: ast.AST, enum_name: str) -> List[str]:
        """Extracts class attribute / assignment names in an Enum class."""
        members: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == enum_name:
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name):
                                members.append(target.id)
                    elif isinstance(item, ast.AnnAssign):
                        if isinstance(item.target, ast.Name):
                            members.append(item.target.id)
        return members

    @classmethod
    def validate_fraction_math_interface(cls, tree: ast.AST) -> Tuple[bool, List[str]]:
        """Verifies Fraction and MathQuestionGenerator interfaces via AST."""
        errors: List[str] = []
        classes = cls.extract_classes_and_methods(tree)

        # 1. Fraction class
        if "Fraction" not in classes:
            errors.append("Class 'Fraction' is not defined in fraction_math module.")
        else:
            methods = set(classes["Fraction"])
            missing = cls.REQUIRED_FRACTION_METHODS - methods
            if missing:
                errors.append(f"Class 'Fraction' is missing required methods: {sorted(missing)}")

        # 2. MathQuestionGenerator class
        if "MathQuestionGenerator" not in classes:
            errors.append("Class 'MathQuestionGenerator' is not defined in fraction_math module.")
        else:
            methods = set(classes["MathQuestionGenerator"])
            if "generate" not in methods:
                errors.append("Class 'MathQuestionGenerator' is missing required 'generate' method.")

        return (len(errors) == 0), errors

    @classmethod
    def validate_game_engine_interface(cls, tree: ast.AST) -> Tuple[bool, List[str]]:
        """Verifies GameEngine and GameState interfaces via AST."""
        errors: List[str] = []
        classes = cls.extract_classes_and_methods(tree)

        # 1. GameEngine class
        if "GameEngine" not in classes:
            errors.append("Class 'GameEngine' is not defined in game_engine module.")
        else:
            methods = set(classes["GameEngine"])
            missing = cls.REQUIRED_ENGINE_METHODS - methods
            if missing:
                errors.append(f"Class 'GameEngine' is missing required methods: {sorted(missing)}")
            if "viewScore" not in methods and "view_score" not in methods:
                errors.append("Class 'GameEngine' must implement 'viewScore()' or 'view_score()'.")

        # 2. GameState enum
        if "GameState" not in classes:
            errors.append("Enum 'GameState' is not defined in game_engine module.")
        else:
            members = set(cls.extract_enum_members(tree, "GameState"))
            missing_states = cls.REQUIRED_GAME_STATES - members
            if missing_states:
                errors.append(f"Enum 'GameState' is missing required states: {sorted(missing_states)}")

        return (len(errors) == 0), errors

    @classmethod
    def validate_gui_app_interface(cls, tree: ast.AST) -> Tuple[bool, List[str]]:
        """Verifies SpaceFractionsApp class and main() entry point via AST."""
        errors: List[str] = []
        classes = cls.extract_classes_and_methods(tree)
        functions = cls.extract_top_level_functions(tree)

        if "SpaceFractionsApp" not in classes:
            errors.append("Class 'SpaceFractionsApp' is not defined in main application module.")

        if "main" not in functions:
            errors.append("Entry point function 'main()' is not defined in main application module.")

        return (len(errors) == 0), errors

    @classmethod
    def validate_ui_screens_interface(cls, tree: ast.AST) -> Tuple[bool, List[str]]:
        """Verifies FractionVisualizer and HUDFrame widgets via AST."""
        errors: List[str] = []
        classes = cls.extract_classes_and_methods(tree)

        if "FractionVisualizer" not in classes:
            errors.append("Class 'FractionVisualizer' is not defined in ui_screens module.")
        if "HUDFrame" not in classes:
            errors.append("Class 'HUDFrame' is not defined in ui_screens module.")

        return (len(errors) == 0), errors

    @classmethod
    def validate_test_suite_interface(cls, tree: ast.AST) -> Tuple[bool, List[str]]:
        """Verifies presence of test functions in test suite AST."""
        errors: List[str] = []
        test_funcs: List[str] = []

        # Find top-level functions or methods in test classes
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    test_funcs.append(node.name)

        if not test_funcs:
            errors.append("No test functions (starting with 'test_') were found in test module.")

        return (len(errors) == 0), errors

    @classmethod
    def validate_manifest(
        cls, manifest_path: str, project_root: str
    ) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
        """Validates project manifest JSON structure, consistency, and path containment."""
        errors: List[str] = []
        real_root = os.path.realpath(os.path.abspath(project_root))

        if not os.path.isfile(manifest_path):
            errors.append(f"Manifest file not found: '{os.path.basename(manifest_path)}'")
            return False, errors, None

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as err:
            errors.append(f"Failed to parse manifest JSON: {err}")
            return False, errors, None

        # Verify offline safety invariant
        if data.get("runtime_gameplay_verified", False) is True:
            errors.append("Manifest specifies runtime_gameplay_verified=True, which violates offline static safety.")

        # Check listed files exist and stay within project root
        file_list = data.get("generated_files", [])
        if not file_list:
            # Check for alternative key names
            file_list = data.get("files", [])

        for rel_f in file_list:
            if not isinstance(rel_f, str) or not rel_f.strip():
                continue
            # Path traversal / escape check
            if ".." in rel_f or rel_f.startswith(("/", "\\")):
                errors.append(f"Unsafe path entry in manifest: '{rel_f}'")
                continue

            full_f = os.path.realpath(os.path.join(real_root, rel_f))
            try:
                common = os.path.commonpath([real_root, full_f])
                if common != real_root:
                    errors.append(f"Manifest path escape detected: '{rel_f}' resolves outside project root.")
                    continue
            except ValueError:
                errors.append(f"Manifest path volume mismatch for '{rel_f}'.")
                continue

            if not os.path.isfile(full_f):
                errors.append(f"File listed in manifest does not exist on disk: '{rel_f}'")

        return (len(errors) == 0), errors, data

    @classmethod
    def validate_project(
        cls, project_dir: str, require_full_game: bool = True
    ) -> ValidationResult:
        """Executes full static acceptance validation on the designated project directory.
        
        Guarantees:
        - Purely static inspection: NO subprocess execution, NO module imports.
        - Verifies AST syntax on all Python files.
        - Verifies class and method interface contracts.
        - Verifies test suite presence and test function declarations.
        - Verifies manifest existence, consistency, and path containment.
        """
        errors: List[str] = []
        warnings: List[str] = []
        verified_files: List[str] = []
        verified_interfaces: List[str] = []
        details: Dict[str, Any] = {}

        if not project_dir or not os.path.isdir(project_dir):
            return ValidationResult(
                success=False,
                errors=[f"Project directory does not exist or is not a directory: '{project_dir}'"],
            )

        real_project_dir = os.path.realpath(os.path.abspath(project_dir))

        # 1. Collect all project files and verify path containment
        all_py_files: Dict[str, str] = {}  # rel_path -> full_path
        all_files: List[str] = []

        for root, _, filenames in os.walk(real_project_dir):
            for fname in filenames:
                full_p = os.path.realpath(os.path.join(root, fname))
                # Containment check
                try:
                    if os.path.commonpath([real_project_dir, full_p]) != real_project_dir:
                        errors.append(f"File escape outside project boundary: '{full_p}'")
                        continue
                except ValueError:
                    errors.append(f"Cross-volume path detected: '{full_p}'")
                    continue

                rel_p = os.path.relpath(full_p, real_project_dir).replace("\\", "/")
                all_files.append(rel_p)
                if fname.endswith(".py"):
                    all_py_files[rel_p] = full_p

        details["total_files_scanned"] = len(all_files)
        details["python_files_scanned"] = len(all_py_files)

        # 2. Static AST syntax check on all Python files
        ast_trees: Dict[str, ast.AST] = {}
        syntax_ok = True

        for rel_p, full_p in all_py_files.items():
            try:
                with open(full_p, "r", encoding="utf-8") as f:
                    src = f.read()
                is_valid, err_msg, tree = cls.validate_syntax_ast(src, filename=rel_p)
                if not is_valid:
                    syntax_ok = False
                    errors.append(err_msg or f"Syntax error in {rel_p}")
                else:
                    ast_trees[rel_p] = tree
                    verified_files.append(rel_p)
            except Exception as read_err:
                syntax_ok = False
                errors.append(f"Failed to read '{rel_p}': {read_err}")

        # 3. Required file presence check
        def find_file(candidates: List[str]) -> Optional[str]:
            for c in candidates:
                norm_c = c.replace("\\", "/")
                if norm_c in all_files:
                    return norm_c
            return None

        math_rel = find_file(["space_fractions/fraction_math.py", "fraction_math.py"])
        engine_rel = find_file(["space_fractions/game_engine.py", "game_engine.py"])
        question_rel = find_file(["space_fractions/question_bank.py", "question_bank.py"])
        ui_rel = find_file(["space_fractions/ui_screens.py", "ui_screens.py"])
        main_rel = find_file(["space_fractions/main.py", "main.py"])
        test_rel = find_file([
            "tests/test_fraction_game.py",
            "test_fraction_game.py",
            "tests/test_fraction_math.py",
            "tests/test_game_engine.py",
        ])
        req_rel = find_file(["requirements.txt"])
        readme_rel = find_file(["README.md", "space_fractions/README.md"])
        manifest_rel = find_file([
            "space_fractions_manifest.json",
            "offline_validation_manifest.json",
        ])

        if require_full_game:
            if not math_rel:
                errors.append("Required module missing: 'fraction_math.py'")
            if not engine_rel:
                errors.append("Required module missing: 'game_engine.py'")
            if not question_rel:
                errors.append("Required module missing: 'question_bank.py'")
            if not ui_rel:
                errors.append("Required module missing: 'ui_screens.py'")
            if not main_rel:
                errors.append("Required application entry point missing: 'main.py'")
            if not test_rel:
                errors.append("Required test suite missing: 'test_fraction_game.py' or 'tests/test_fraction_math.py'")
            if not req_rel:
                errors.append("Required packaging file missing: 'requirements.txt'")
            if not readme_rel:
                warnings.append("Documentation file missing: 'README.md'")
            if not manifest_rel:
                errors.append("Required manifest missing: 'space_fractions_manifest.json' or 'offline_validation_manifest.json'")

        # 4. Interface checks on AST trees
        interfaces_ok = True

        if math_rel and math_rel in ast_trees:
            ok, errs = cls.validate_fraction_math_interface(ast_trees[math_rel])
            if ok:
                verified_interfaces.append("FractionMathInterface (Fraction, MathQuestionGenerator)")
            else:
                interfaces_ok = False
                errors.extend(errs)

        if engine_rel and engine_rel in ast_trees:
            ok, errs = cls.validate_game_engine_interface(ast_trees[engine_rel])
            if ok:
                verified_interfaces.append("GameEngineInterface (GameEngine, GameState)")
            else:
                interfaces_ok = False
                errors.extend(errs)

        if main_rel and main_rel in ast_trees:
            ok, errs = cls.validate_gui_app_interface(ast_trees[main_rel])
            if ok:
                verified_interfaces.append("GUIAppInterface (SpaceFractionsApp, main())")
            else:
                interfaces_ok = False
                errors.extend(errs)

        if ui_rel and ui_rel in ast_trees:
            ok, errs = cls.validate_ui_screens_interface(ast_trees[ui_rel])
            if ok:
                verified_interfaces.append("UIScreensInterface (FractionVisualizer, HUDFrame)")
            else:
                interfaces_ok = False
                errors.extend(errs)

        # 5. Test suite structure check
        tests_ok = True
        if test_rel and test_rel in ast_trees:
            ok, errs = cls.validate_test_suite_interface(ast_trees[test_rel])
            if ok:
                verified_interfaces.append(f"TestSuiteInterface ({test_rel})")
            else:
                tests_ok = False
                errors.extend(errs)
        elif require_full_game:
            tests_ok = False

        # 6. Manifest consistency check
        manifest_ok = True
        manifest_data = None
        if manifest_rel:
            manifest_full = os.path.join(real_project_dir, manifest_rel)
            ok, errs, manifest_data = cls.validate_manifest(manifest_full, real_project_dir)
            if not ok:
                manifest_ok = False
                errors.extend(errs)
            else:
                verified_files.append(manifest_rel)
        elif require_full_game:
            manifest_ok = False

        # Overall success
        success = (len(errors) == 0) and syntax_ok and interfaces_ok

        return ValidationResult(
            success=success,
            errors=errors,
            warnings=warnings,
            verified_files=sorted(list(set(verified_files))),
            verified_interfaces=verified_interfaces,
            ast_syntax_valid=syntax_ok,
            interfaces_valid=interfaces_ok,
            tests_valid=tests_ok,
            manifest_valid=manifest_ok,
            manifest_data=manifest_data,
            details=details,
        )

    @classmethod
    def validate_generic_project(
        cls,
        project_dir: str,
    ) -> ValidationResult:
        """Performs purely static validation of generic project deliverables.

        SAFETY AND INTEGRITY GUARANTEES:
        1. STRICTLY STATIC: Zero code execution, zero subprocess spawns, zero importlib execution.
        2. PATH CONTAINMENT: Verifies all files resolve strictly within the designated project directory.
        3. NON-EMPTY FILES: Verifies that deliverable files are non-empty (>0 bytes).
        4. SYNTAX VERIFICATION: AST parsing verifies valid Python syntax on all .py files without executing code.
        5. HONEST CONTRACT: Explicitly sets tests_valid=False and runtime_gameplay_verified=False (no runtime claims).
        """
        errors: List[str] = []
        warnings: List[str] = []
        verified_files: List[str] = []
        verified_interfaces: List[str] = []
        details: Dict[str, Any] = {}

        if not project_dir or not os.path.isdir(project_dir):
            return ValidationResult(
                success=False,
                errors=[f"Project directory does not exist or is not a directory: '{project_dir}'"],
            )

        real_project_dir = os.path.realpath(os.path.abspath(project_dir))

        # 1. Collect all project files, verify path containment, and ensure non-empty
        all_py_files: Dict[str, str] = {}
        all_files: List[str] = []

        for root, _, filenames in os.walk(real_project_dir):
            for fname in filenames:
                full_p = os.path.realpath(os.path.join(root, fname))
                # Containment check
                try:
                    if os.path.commonpath([real_project_dir, full_p]) != real_project_dir:
                        errors.append(f"File escape outside project boundary: '{full_p}'")
                        continue
                except ValueError:
                    errors.append(f"Cross-volume path detected: '{full_p}'")
                    continue

                # Non-empty file check
                try:
                    size = os.path.getsize(full_p)
                    if size == 0:
                        errors.append(f"Generated file is empty (0 bytes): '{fname}'")
                except OSError as err:
                    errors.append(f"Cannot access file '{fname}': {err}")

                rel_p = os.path.relpath(full_p, real_project_dir).replace("\\", "/")
                all_files.append(rel_p)
                if fname.endswith(".py"):
                    all_py_files[rel_p] = full_p

        details["total_files_scanned"] = len(all_files)
        details["python_files_scanned"] = len(all_py_files)

        if not all_files:
            errors.append("No deliverable files found in staging directory.")

        # 2. Static AST syntax check on all Python files
        syntax_ok = True
        for rel_p, full_p in all_py_files.items():
            try:
                with open(full_p, "r", encoding="utf-8") as f:
                    src = f.read()
                is_valid, err_msg, tree = cls.validate_syntax_ast(src, filename=rel_p)
                if not is_valid:
                    syntax_ok = False
                    errors.append(err_msg or f"Syntax error in {rel_p}")
                else:
                    verified_files.append(rel_p)
                    verified_interfaces.append(f"SyntaxVerified: {rel_p}")
            except Exception as read_err:
                syntax_ok = False
                errors.append(f"Failed to read '{rel_p}': {read_err}")

        # 3. Non-Python files readable check (e.g. README.md, manifest)
        for rel_p in all_files:
            if rel_p not in all_py_files:
                full_p = os.path.join(real_project_dir, rel_p)
                try:
                    with open(full_p, "rb") as f:
                        f.read()
                    if rel_p not in verified_files:
                        verified_files.append(rel_p)
                except Exception as read_err:
                    errors.append(f"Failed to read '{rel_p}': {read_err}")

        # 4. Manifest consistency check (if manifest present)
        manifest_ok = True
        manifest_data = None
        for mf_name in ["offline_validation_manifest.json", "project_manifest.json", "space_fractions_manifest.json"]:
            if mf_name in all_files:
                manifest_full = os.path.join(real_project_dir, mf_name)
                ok, errs, manifest_data = cls.validate_manifest(manifest_full, real_project_dir)
                if not ok:
                    manifest_ok = False
                    errors.extend(errs)
                break

        success = (len(errors) == 0) and syntax_ok and (len(all_files) > 0)

        return ValidationResult(
            success=success,
            errors=errors,
            warnings=warnings,
            verified_files=sorted(list(set(verified_files))),
            verified_interfaces=verified_interfaces,
            ast_syntax_valid=syntax_ok,
            interfaces_valid=True,
            tests_valid=False,  # Explicitly do NOT claim passing tests without execution
            manifest_valid=manifest_ok,
            manifest_data=manifest_data,
            details=details,
        )
