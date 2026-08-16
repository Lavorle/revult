"""
AC5 the_question bootstrap probe under renpy-host embed.

Gate name: the_question  (RENPY_HOST_GATE=the_question)

Sets RENPY_HOST_GAME to <base>/the_question (if unset) and reuses the
bootstrap progressive stages. Report is still written to
host/target/gate-bootstrap.txt (shared format) plus a thin alias at
host/target/gate-the_question.txt.
"""

import os
import runpy
import sys
from pathlib import Path


def _base_dir() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "renpy").is_dir() and (p / "host" / "README.md").is_file():
            return p
    return here


base = _base_dir()
os.environ.setdefault("RENPY_HOST_BASE", str(base))
os.environ.setdefault("RENPY_HOST_BUILD", "1")

tq = base / "the_question"
if tq.is_dir():
    os.environ.setdefault("RENPY_HOST_GAME", str(tq))

# Execute bootstrap.py in this process (same embed, same renpy_host).
# py.run (host run_file) does not define __file__; resolve via RENPY_HOST_BASE.
_base_gates = Path(os.environ.get("RENPY_HOST_BASE", str(base))) / "host" / "python" / "gates"
gates = _base_gates if _base_gates.is_dir() else Path.cwd()
bootstrap = gates / "bootstrap.py"
# Ensure gates dir on path (run_file already inserts it, but be defensive).
if str(gates) not in sys.path:
    sys.path.insert(0, str(gates))

# run_path executes bootstrap which always writes gate-bootstrap.txt and
# calls request_quit. Mirror a short alias report afterward if bootstrap
# left the file.
err = None
try:
    runpy.run_path(str(bootstrap), run_name="__the_question_gate__")
except BaseException as e:
    err = e

# Alias report for the_question gate consumers.
src = base / "host" / "target" / "gate-bootstrap.txt"
dst = base / "host" / "target" / "gate-the_question.txt"
dst.parent.mkdir(parents=True, exist_ok=True)
if src.is_file():
    text = src.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    if "gate=the_question" not in text:
        text += "gate=the_question\n"
    dst.write_text(text, encoding="utf-8")
else:
    dst.write_text(
        "reached_stage=init\nmissing=['bootstrap']\ntraceback=\nok=False\n"
        "notes=bootstrap did not write gate-bootstrap.txt\ngate=the_question\n",
        encoding="utf-8",
    )

if err is not None:
    raise err
