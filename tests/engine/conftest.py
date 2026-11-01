"""Puts engine/ on the path so `import mmm` and `import mms2c` work under pytest from
the repository root, the way they do for the app's engine process (both packages sit in
engine/, which is also where the engine starts the child processes of its rival conversions)."""
import os
import sys

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)
