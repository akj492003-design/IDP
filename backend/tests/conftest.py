"""Load isolated backend service modules without requiring the full Flask runtime."""
import sys
import types
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT / "app"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# The application package __init__ imports Flask and all service dependencies.
# These unit tests target a pure-Python extraction module, so expose its package
# path without executing the application factory.
if "app" not in sys.modules:
    app_package = types.ModuleType("app")
    app_package.__path__ = [str(APP_ROOT)]
    sys.modules["app"] = app_package
