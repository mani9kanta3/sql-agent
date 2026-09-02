"""
Make the Windows console able to print a rupee sign.

Python on Windows defaults stdout to cp1252, which has no character for
the rupee sign. The agent answers questions about an Indian shop, so
nearly every answer it writes contains one, and printing it crashed the
command line script with a UnicodeEncodeError after the whole question
had already been answered correctly.

The API never had this problem, because JSON goes out as UTF-8 over the
socket regardless. It is only the terminal.

Called explicitly from the two scripts that print answers rather than
being done on import, because a module quietly reconfiguring stdout as a
side effect of being imported is the kind of thing that is very annoying
to track down later.
"""

import sys


def use_utf8():
    """Switch stdout and stderr to UTF-8 if they are not already."""
    for stream in (sys.stdout, sys.stderr):
        # reconfigure() exists on 3.7+, but not on every stream object.
        # A redirected or wrapped stream may not have it, and that is not
        # worth failing over.
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
