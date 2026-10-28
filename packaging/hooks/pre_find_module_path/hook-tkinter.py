"""Keep tkinter discoverable when the host Tcl probe is unavailable.

The release spec explicitly supplies a matching Tcl/Tk runtime and library data.
"""


def pre_find_module_path(hook_api):
    """Retain the interpreter's ordinary tkinter module search path."""
