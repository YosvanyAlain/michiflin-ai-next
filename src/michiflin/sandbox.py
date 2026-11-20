from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .errors import AppError


@dataclass(frozen=True)
class Policy:
    wall_seconds: int = 5
    cpu_seconds: int = 3
    memory_bytes: int = 128 * 1024 * 1024
    temporary_bytes: int = 8 * 1024 * 1024
    output_bytes: int = 64000

    def __post_init__(self):
        for value, low, high in ((self.wall_seconds, 1, 60), (self.cpu_seconds, 1, 30),
                                 (self.memory_bytes, 64 * 1024 * 1024, 256 * 1024 * 1024),
                                 (self.temporary_bytes, 1024 * 1024, 32 * 1024 * 1024),
                                 (self.output_bytes, 1024, 128000)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid sandbox resource policy")


def _filter(target) -> None:
    """Deny process creation, networking and kernel control, including alternate syscall architectures."""
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise AppError("sandbox_unavailable")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; bad syscall architectures are killed.
    if not context:
        raise AppError("sandbox_unavailable")
    try:
        for name in ("clone", "clone3", "fork", "vfork", "unshare", "setns", "mount", "umount2", "pivot_root",
                     "chroot", "ptrace", "process_vm_readv", "process_vm_writev", "bpf", "keyctl", "add_key",
                     "request_key", "perf_event_open", "socket", "socketpair", "connect", "bind", "listen",
                     "accept", "accept4", "io_uring_setup", "open_by_handle_at", "userfaultfd", "reboot"):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise AppError("sandbox_unavailable")
        if seccomp.seccomp_export_bpf(context, target.fileno()) != 0:
            raise AppError("sandbox_unavailable")
    finally:
        seccomp.seccomp_release(context)
    target.seek(0)


@contextmanager
def _input_file(stream):
    """Reopen a trusted regular-file descriptor without exposing its host path or sharing its offset."""
    if stream is None:
        yield None
        return
    try:
        descriptor = stream.fileno()
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1 or not 0 <= before.st_size <= 16 * 1024 * 1024:
            raise AppError("invalid_sandbox_file")
        reopened = os.open(f"/proc/self/fd/{descriptor}", os.O_RDONLY | os.O_CLOEXEC)
    except (AttributeError, OSError, TypeError, ValueError):
        raise AppError("invalid_sandbox_file") from None
    with os.fdopen(reopened, "rb") as source:
        after = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise AppError("invalid_sandbox_file")
        yield source


def run(script: str, payload: dict, *, policy: Policy | None = None,
        stop: threading.Event | None = None, tick: Callable[[], None] | None = None, input_file=None) -> dict:
    """Run Python offline in Linux namespaces. No credentials, host workspace or child processes.

    This primitive is not a general user-facing code execution operation. Callers must enforce
    admission, identity binding, reviewed handler selection and output semantics separately.
    """
    policy = policy or Policy()
    if not isinstance(script, str) or len(script.encode()) > 32000 or not isinstance(payload, dict):
        raise AppError("invalid_sandbox_input")
    try:
        encoded = json.dumps(payload, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise AppError("invalid_sandbox_input") from None
    if len(encoded) > 16000:
        raise AppError("invalid_sandbox_input")
    if stop is not None and stop.is_set():
        raise AppError("sandbox_cancelled")
    if sys.platform != "linux" or not shutil.which("bwrap") or not shutil.which("prlimit"):
        raise AppError("sandbox_unavailable")
    # Anonymous descriptors avoid named artifacts, leaked prompts and cross-job work directories.
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as request, \
            tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as rules, _input_file(input_file) as file_input:
        source.write(script.encode())
        source.seek(0)
        request.write(encoded)
        request.seek(0)
        _filter(rules)
        command = [shutil.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                   "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin", "--setenv", "HOME", "/tmp",
                   "--setenv", "LANG", "C.UTF-8", "--ro-bind", "/usr", "/usr"]
        runtime = str(Path(sys.base_prefix).resolve())
        if runtime == "/" or runtime.startswith(("/home/", "/root/", "/tmp/")):
            raise AppError("sandbox_unavailable")
        if not runtime.startswith("/usr/") and runtime != "/usr":
            command.extend(["--ro-bind", runtime, runtime])
        library_path = sysconfig.get_config_var("LIBDIR")
        if not isinstance(library_path, str) or not Path(library_path).is_relative_to(runtime):
            raise AppError("sandbox_unavailable")
        command.extend(["--setenv", "LD_LIBRARY_PATH", library_path])
        for path in ("/lib", "/lib64", "/bin"):
            if os.path.isdir(path):
                command.extend(["--ro-bind", path, path])
        descriptors = (source.fileno(), rules.fileno())
        if file_input is not None:
            command.extend(["--ro-bind-data", str(file_input.fileno()), "/input.bin"])
            descriptors += (file_input.fileno(),)
        command.extend(["--dev", "/dev", "--remount-ro", "/dev", "--size", str(policy.temporary_bytes),
                        "--tmpfs", "/tmp", "--ro-bind-data", str(source.fileno()), "/handler.py",
                        "--remount-ro", "/", "--chdir", "/tmp", "--seccomp", str(rules.fileno()), "--",
                        shutil.which("prlimit"), f"--as={policy.memory_bytes}", f"--cpu={policy.cpu_seconds}",
                        f"--fsize={max(policy.output_bytes, policy.temporary_bytes)}", "--nofile=32", "--core=0", "--",
                        sys._base_executable, "-I", "-S", "-B", "/handler.py"])
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, stdin=request, stdout=output, stderr=subprocess.DEVNULL,
                                       env={"PATH": "/usr/local/bin:/usr/bin:/bin"}, close_fds=True,
                                       pass_fds=descriptors, start_new_session=True)
        except OSError:
            raise AppError("sandbox_unavailable") from None
        failure = None
        try:
            while process.poll() is None:
                if tick is not None:
                    tick()
                if stop is not None and stop.is_set():
                    failure = "sandbox_cancelled"
                elif time.monotonic() - started >= policy.wall_seconds:
                    failure = "sandbox_timeout"
                elif os.fstat(output.fileno()).st_size > policy.output_bytes:
                    failure = "sandbox_output_limit"
                if failure:
                    break
                # Waits are bounded; no background worker survives the caller.
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.02)
        finally:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if failure:
            raise AppError(failure)
        output.seek(0)
        raw = output.read(policy.output_bytes + 1)
        if len(raw) > policy.output_bytes:
            raise AppError("sandbox_output_limit")
        if process.returncode != 0:
            raise AppError("sandbox_execution_failed")
        try:
            result = json.loads(raw)
            json.dumps(result, allow_nan=False)
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise AppError("sandbox_result_invalid") from None
        if not isinstance(result, dict):
            raise AppError("sandbox_result_invalid")
        return result


def available() -> bool:
    try:
        return run('print("{}")', {}) == {}
    except AppError:
        return False
