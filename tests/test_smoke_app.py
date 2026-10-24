"""Unit tests for PackagingSmokeApp GUI logic.

Tests window initialization, label binding, and click event handlers
using Tkinter without blocking the event loop.
"""

import tkinter as tk
import pytest
from packaging_poc.smoke_app import PackagingSmokeApp


@pytest.fixture(scope="module")
def tk_root():
    """Provides an initialized hidden Tk root window reused across module tests."""
    try:
        root = tk.Tk()
        root.withdraw()  # Hide during test execution
        yield root
        root.destroy()
    except tk.TclError as err:
        pytest.skip(f"Tkinter display unavailable in test environment: {err}")


def test_smoke_app_initialization(tk_root):
    """Validates initial window title and status label value."""
    app = PackagingSmokeApp(tk_root)
    assert tk_root.title() == "Packaging Proof-of-Concept: Smoke Test"
    assert app.status_var.get() == "Status: Initialized. Ready for interaction."
    assert app.click_count == 0


def test_smoke_app_button_click_response(tk_root):
    """Validates that invoking the button handler updates state and label text."""
    app = PackagingSmokeApp(tk_root)
    app.on_verify_click()
    assert app.click_count == 1
    assert "Verified! Button clicked 1 time(s)" in app.status_var.get()

    app.on_verify_click()
    assert app.click_count == 2
    assert "Verified! Button clicked 2 time(s)" in app.status_var.get()
