import os
import tempfile


def pytest_configure(config):  # noqa: ARG001
    """Point HUESYNC_CONFIG at a writable temp path before any module imports app.py.

    app.py creates Storage(CONFIG_PATH) at module level, which calls mkdir().
    Without this, importing the test module in a read-only environment (e.g. CI)
    raises PermissionError on /etc/huesync.
    """
    if "HUESYNC_CONFIG" not in os.environ:
        tmpdir = tempfile.mkdtemp(prefix="huesync_test_")
        os.environ["HUESYNC_CONFIG"] = os.path.join(tmpdir, "config.json")
