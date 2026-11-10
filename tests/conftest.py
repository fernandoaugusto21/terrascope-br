"""Keep config and cache writes of the test run out of the user's home.

The config module reads these variables once, at import time."""

import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="terrascope-br-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_ROOT, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_ROOT, "cache")
os.environ.pop("terrascope_CONFIG", None)
