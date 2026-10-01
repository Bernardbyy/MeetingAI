"""Updating from inside the app: Settings -> Updates.

Check for updates asks GitHub what is new (the only time the app contacts
anything but Ollama, and only when someone presses the button). Update and
restart then hands over to Batch/apply-update.bat, run from a copy in %TEMP%:
it waits for the app to close, pulls, runs after-update.bat (packages and
models) from the new version, and starts the app again. The copy matters:
git pull would otherwise rewrite the very batch file cmd is running.
"""

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).parent.parent
NO_PROMPTS = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


class UpdateError(RuntimeError):
    pass


def _git(*args, root: Path = ROOT, timeout: int = 60) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, **NO_PROMPTS})
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"git did not run: {e}") from e
    if r.returncode != 0:
        raise UpdateError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def current(root: Path = ROOT) -> dict:
    """The installed version, for the Settings panel."""
    try:
        commit, date = _git("log", "-1", "--format=%h%x09%cs", root=root).split("\t")
        return {"commit": commit, "date": date}
    except (UpdateError, ValueError):
        return {"commit": None, "date": None}


def check(root: Path = ROOT) -> dict:
    """What updating would bring in, newest first."""
    try:
        _git("fetch", "--quiet", root=root)
    except UpdateError as e:
        raise UpdateError(f"Could not check for updates. Are you online? ({e})") from e
    log = _git("log", "--format=%h%x09%s", "HEAD..@{u}", root=root)
    changes = [dict(zip(("commit", "subject"), line.split("\t", 1)))
               for line in log.splitlines() if line]
    # Edited files make git pull refuse; say so before anyone presses Update.
    edited = bool(_git("status", "--porcelain", "--untracked-files=no", root=root))
    return {"current": current(root), "changes": changes, "local_edits": edited}


def apply(port: int, root: Path = ROOT, exit_after: float = 1.0) -> None:
    """Start the updater in its own window, then close the app so its files
    are free. The updater restarts the app when it is done."""
    script = Path(tempfile.gettempdir()) / "meetingai-apply-update.bat"
    shutil.copyfile(root / "Batch" / "apply-update.bat", script)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)  # Windows: a window to watch
    subprocess.Popen(["cmd", "/c", str(script), str(root.resolve()), str(port)],
                     cwd=root, creationflags=flags)
    # Leave time for the response to reach the browser before going down.
    threading.Timer(exit_after, os._exit, [0]).start()
