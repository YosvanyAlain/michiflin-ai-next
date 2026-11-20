import os
import tempfile
import threading
import time
from dataclasses import replace

import pytest

from michiflin import sandbox
from michiflin.errors import AppError


@pytest.fixture(scope="module")
def isolated():
    if not sandbox.available():
        if os.environ.get("MICHIFLIN_REQUIRE_SANDBOX") == "1":
            pytest.fail("Required namespace/seccomp sandbox is unavailable")
        pytest.skip("Linux bubblewrap/seccomp isolation unavailable; execution stays disabled")


def test_no_execution_when_isolation_is_missing(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    with pytest.raises(AppError, match="sandbox_unavailable"):
        sandbox.run('print("{}")', {})


def test_missing_seccomp_fails_closed(monkeypatch):
    monkeypatch.setattr(sandbox.ctypes.util, "find_library", lambda _name: None)
    with pytest.raises(AppError, match="sandbox_unavailable"), tempfile.TemporaryFile() as target:
        sandbox._filter(target)


@pytest.mark.parametrize("kwargs", [{"wall_seconds": 0}, {"cpu_seconds": 31}, {"memory_bytes": 1024},
                                    {"output_bytes": 128001}, {"temporary_bytes": 0}, {"wall_seconds": True}])
def test_resource_policy_has_hard_bounds(kwargs):
    with pytest.raises(ValueError):
        sandbox.Policy(**kwargs)


def test_real_offline_execution_with_bounded_json_input(isolated):
    script = 'import json,sys; x=json.load(sys.stdin); print(json.dumps({"sum":sum(x["values"])}))'
    assert sandbox.run(script, {"values": [1, 2, 3]}) == {"sum": 6}


def test_host_secrets_workspace_and_environment_are_not_visible(isolated, tmp_path, monkeypatch):
    secret = tmp_path / "private-token.txt"
    secret.write_text("synthetic-secret")
    monkeypatch.setenv("SYNTHETIC_PRIVATE_API_KEY", "synthetic-secret")
    script = '''import json,os,sys
value=json.load(sys.stdin)
print(json.dumps({"secret":os.path.exists(value["path"]),"env":dict(os.environ),
                  "home":os.path.exists("/home"),"root":os.path.exists("/root"),
                  "etc":os.path.exists("/etc"),"proc":os.path.exists("/proc"),
                  "cwd":os.getcwd()}))
'''
    result = sandbox.run(script, {"path": str(secret)})
    assert result["secret"] is False
    assert all(result[name] is False for name in ("home", "root", "etc", "proc"))
    assert "SYNTHETIC_PRIVATE_API_KEY" not in result["env"]
    assert result["cwd"] == "/tmp"


def test_no_network_process_creation_or_namespace_escape(isolated):
    script = '''import json,os,socket,ctypes
result={}
for name,call in [("socket",lambda:socket.socket()),("fork",os.fork),
                  ("unshare",lambda:ctypes.CDLL(None).unshare(0x10000000))]:
    try:
        value=call()
        result[name]=value if isinstance(value,int) else "unexpected"
    except OSError as error:
        result[name]=error.errno
print(json.dumps(result))
'''
    result = sandbox.run(script, {})
    assert result == {"socket": 1, "fork": 1, "unshare": -1}


def test_root_and_devices_cannot_be_used_as_unbounded_scratch(isolated):
    script = '''import json
result={}
for path in ("/escape", "/dev/escape", "/usr/escape"):
    try:
        open(path,"wb").close()
        result[path]="unexpected"
    except OSError as error:
        result[path]=error.errno
print(json.dumps(result))
'''
    assert set(sandbox.run(script, {}).values()) == {30}


def test_bounded_tmpfs_and_no_cross_job_files(isolated):
    script = '''import json
try:
    with open("/tmp/fill","wb",buffering=0) as output:
        for _ in range(100): output.write(b"x"*65536)
    print("{}")
except OSError as error:
    print(json.dumps({"error":error.errno}))
'''
    result = sandbox.run(script, {}, policy=replace(sandbox.Policy(), temporary_bytes=1024 * 1024))
    assert result["error"] in (27, 28)  # File-size limit or tmpfs aggregate limit.
    assert sandbox.run('import os,json; print(json.dumps({"files":os.listdir("/tmp")}))', {}) == {"files": []}


def test_memory_limit_is_real(isolated):
    script = '''import json
try:
    value=bytearray(300*1024*1024)
    print("{}")
except MemoryError:
    print(json.dumps({"limited":True}))
'''
    assert sandbox.run(script, {}) == {"limited": True}


def test_wall_timeout_reaps_worker(isolated):
    started = time.monotonic()
    with pytest.raises(AppError, match="sandbox_timeout"):
        sandbox.run('import time; time.sleep(30)', {}, policy=replace(sandbox.Policy(), wall_seconds=1))
    assert time.monotonic() - started < 4


def test_cpu_limit_is_real(isolated):
    started = time.monotonic()
    with pytest.raises(AppError, match="sandbox_execution_failed"):
        sandbox.run('while True: pass', {}, policy=replace(sandbox.Policy(), cpu_seconds=1, wall_seconds=5))
    assert time.monotonic() - started < 4


def test_cancellation_reaps_worker(isolated):
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    try:
        with pytest.raises(AppError, match="sandbox_cancelled"):
            sandbox.run('import time; time.sleep(30)', {}, stop=stop)
    finally:
        timer.join()


def test_cancelled_work_never_starts(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **kw: pytest.fail("Must not start"))
    with pytest.raises(AppError, match="sandbox_cancelled"):
        sandbox.run('print("{}")', {}, stop=stop)


def test_output_limit_and_invalid_results(isolated):
    with pytest.raises(AppError, match="sandbox_output_limit"):
        sandbox.run('print("x"*200000)', {}, policy=replace(sandbox.Policy(), output_bytes=1024))
    for script in ('print("not JSON")', 'print("[]")', 'print(\'{"number":NaN}\')'):
        with pytest.raises(AppError, match="sandbox_result_invalid"):
            sandbox.run(script, {})


def test_exceptions_never_disclose_worker_private_error_text(isolated):
    with pytest.raises(AppError, match="^sandbox_execution_failed$"):
        sandbox.run('raise RuntimeError("synthetic-private-error")', {})


@pytest.mark.parametrize("payload", [[], None, {"x": float("nan")}, {"x": "x" * 16001}])
def test_invalid_input_rejected_without_execution(payload):
    with pytest.raises(AppError, match="invalid_sandbox_input"):
        sandbox.run('print("{}")', payload)


def test_json_only_output_does_not_execute_python(isolated):
    with pytest.raises(AppError, match="sandbox_result_invalid"):
        sandbox.run('print("__import__(\\\"os\\\").getcwd()")', {})


def test_explicit_file_is_read_only_and_does_not_expose_vault_or_change_parent_offset(isolated, tmp_path):
    source = tmp_path / 'private-file'
    source.write_bytes(b'synthetic private contents')
    (tmp_path / 'sibling-secret').write_bytes(b'must stay private')
    script = '''import hashlib,json,os
with open('/input.bin','rb') as f: data=f.read()
try:
    open('/input.bin','wb').write(b'changed')
    readonly=False
except OSError:
    readonly=True
print(json.dumps({'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                  'readonly':readonly,'root':os.listdir('/')}))
'''
    import hashlib
    with source.open('rb') as stream:
        stream.seek(4)
        result = sandbox.run(script, {}, input_file=stream)
        assert stream.tell() == 4
    assert result['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['bytes'] == source.stat().st_size and result['readonly']
    assert 'sibling-secret' not in result['root']
    assert source.read_bytes() == b'synthetic private contents'
    assert sandbox.run('import os,json; print(json.dumps({"input":os.path.exists("/input.bin")}))', {}) == {'input': False}


def test_oversized_or_nonfile_inputs_are_rejected_before_launch(isolated, tmp_path, monkeypatch):
    import io
    source = tmp_path / 'large'
    with source.open('wb') as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    monkeypatch.setattr(sandbox.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start'))
    for stream in (None, io.BytesIO(b'x'), str(source)):
        if stream is not None:
            with pytest.raises(AppError, match='invalid_sandbox_file'):
                sandbox.run('print("{}")', {}, input_file=stream)
    with source.open('rb') as stream, pytest.raises(AppError, match='invalid_sandbox_file'):
        sandbox.run('print("{}")', {}, input_file=stream)
    with open('/dev/null', 'rb') as stream, pytest.raises(AppError, match='invalid_sandbox_file'):
        sandbox.run('print("{}")', {}, input_file=stream)


def test_large_file_input_is_not_an_inline_json_or_output_limit(isolated, tmp_path):
    source = tmp_path / 'large'
    with source.open('wb') as stream:
        stream.truncate(16 * 1024 * 1024)
    script = '''import hashlib,json
h=hashlib.sha256(); size=0
with open('/input.bin','rb') as f:
    for b in iter(lambda:f.read(65536),b''):
        h.update(b); size+=len(b)
print(json.dumps({'bytes':size,'sha256':h.hexdigest()}))
'''
    with source.open('rb') as stream:
        result = sandbox.run(script, {}, input_file=stream)
    import hashlib
    assert result == {'bytes': 16 * 1024 * 1024, 'sha256': hashlib.sha256(bytes(16 * 1024 * 1024)).hexdigest()}


def test_hardlinked_input_is_rejected_without_starting_worker(isolated, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_bytes(b'synthetic')
    os.link(source, tmp_path / 'link')
    monkeypatch.setattr(sandbox.subprocess, 'Popen', lambda *a, **k: pytest.fail('Must not start'))
    with source.open('rb') as stream, pytest.raises(AppError, match='invalid_sandbox_file'):
        sandbox.run('print("{}")', {}, input_file=stream)
