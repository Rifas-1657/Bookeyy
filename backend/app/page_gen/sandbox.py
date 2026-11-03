"""
app/page_gen/sandbox.py

Python code-block verification for generated pages.

SECURITY MODEL
--------------
Code blocks are written by an LLM (and may be steered by prompt injection in
ingested sources), so executing them is equivalent to running untrusted code.

* Execution is OPT-IN via the ``CODE_RUNNER_ENABLED`` setting (default False).
  When disabled, :func:`syntax_check` is the only check performed — the code is
  parsed with ``ast.parse`` / ``compile()`` and NEVER executed.
* When enabled, :func:`run_code` runs the snippet in a child process with
  defence-in-depth hardening only:
    - ``python -I`` (isolated mode: ignores PYTHON* env vars, user site, cwd on sys.path)
    - an (almost) empty environment — no app secrets are inherited
    - a throw-away temp working directory
    - POSIX ``resource`` limits (CPU, address space, file size, open files, no forking)
    - hard timeout that kills the whole process tree / group
  This is NOT a security boundary. In production, real isolation (a container
  with no network, gVisor, nsjail, firejail, or a remote sandbox service) is
  REQUIRED before turning CODE_RUNNER_ENABLED on.
* Learner-created books never execute code regardless of the setting (the
  course-builder runner passes ``allow_execute=False`` for them).
"""
from __future__ import annotations

import ast
import asyncio
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX_TIMEOUT_S = 5
MAX_OUTPUT_BYTES = 8192
_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024


class SandboxResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int, timed_out: bool) -> None:
        self.stdout = stdout.strip()
        self.stderr = stderr.strip()
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.available = True

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def code_runner_enabled() -> bool:
    from app.config import get_settings
    return bool(getattr(get_settings(), "code_runner_enabled", False))


def syntax_check(code: str) -> str | None:
    """Parse *code* without executing it. Returns an error string or None."""
    try:
        tree = ast.parse(code, filename="<code-block>", mode="exec")
        compile(tree, "<code-block>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    except (ValueError, RecursionError, MemoryError) as exc:  # e.g. null bytes, absurd nesting
        return f"Invalid code: {exc}"
    return None


def _posix_preexec() -> None:  # pragma: no cover - POSIX only
    os.setsid()  # new process group so the whole tree can be killed
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (SANDBOX_TIMEOUT_S, SANDBOX_TIMEOUT_S))
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass


def kill_process_tree(proc) -> None:
    """Kill *proc* and all of its descendants (best effort, cross-platform)."""
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _minimal_env(tmpdir: str) -> dict[str, str]:
    env = {"TEMP": tmpdir, "TMP": tmpdir, "TMPDIR": tmpdir, "HOME": tmpdir,
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.name == "nt" and os.environ.get("SystemRoot"):
        env["SystemRoot"] = os.environ["SystemRoot"]  # required for the CRT / sockets init
    return env


async def run_code(code: str, expected_output: str) -> tuple[str | None, str | None]:
    """
    Execute *code* in a hardened subprocess (ONLY when CODE_RUNNER_ENABLED).
    Returns (actual_output, error) where error is None on success.
    Returns (None, "code runner disabled") when execution is not enabled.
    Returns (None, "sandbox unavailable") if Python executable cannot be found.
    """
    if not code_runner_enabled():
        return None, "code runner disabled"
    python = sys.executable
    if not python:
        return None, "sandbox unavailable: no Python executable"

    with tempfile.TemporaryDirectory(prefix="bookey_sandbox_") as tmpdir:
        code_path = Path(tmpdir) / "code.py"
        code_path.write_text(code, encoding="utf-8")

        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["preexec_fn"] = _posix_preexec

        try:
            proc = await asyncio.create_subprocess_exec(
                python, "-I", "-S", str(code_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmpdir,
                env=_minimal_env(tmpdir),
                **kwargs,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=SANDBOX_TIMEOUT_S
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                kill_process_tree(proc)
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=5)
                except Exception:
                    pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return None, f"Code execution timed out after {SANDBOX_TIMEOUT_S}s"

            stdout = stdout_b[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_b[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            exit_code = proc.returncode

            if exit_code != 0:
                return None, f"Code exited {exit_code}: {stderr[:400] or stdout[:400]}"

            actual = stdout.strip()
            expected = expected_output.strip()
            if actual != expected:
                return actual, f"Output mismatch: expected {expected!r}, got {actual!r}"

            return actual, None

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return None, f"sandbox error: {exc}"
