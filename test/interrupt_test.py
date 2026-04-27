import os
import subprocess
import time
import signal
import socket
import psutil
from contextlib import closing

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def find_free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def is_port_in_use(port):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_related_processes(pgid):
    """Find all processes belonging to the given process group."""
    procs = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if os.getpgid(proc.info["pid"]) == pgid:
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            pass
    return procs


def test_server_interrupt_full_cleanup():
    print("Finding port...")
    port = find_free_port()

    print(f"Starting server via pdm run server on port {port}...")
    env = os.environ.copy()
    env["PALUDORO_PORT"] = str(port)

    import shutil

    pdm_path = shutil.which("pdm")
    if not pdm_path:
        local_pdm = os.path.expanduser("~/.local/bin/pdm")
        if os.path.exists(local_pdm):
            pdm_path = local_pdm
        else:
            assert False, "pdm not found in PATH or ~/.local/bin/pdm"

    proc = subprocess.Popen(
        [pdm_path, "run", "server"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    pgid = os.getpgid(proc.pid)
    print(f"Server PGID: {pgid}")

    print("Waiting for startup...")
    for _ in range(20):
        time.sleep(0.5)
        if is_port_in_use(port):
            print(f"Port {port} is now in use.")
            break
    else:
        proc.kill()
        assert False, f"Server failed to start on port {port}"

    print(f"Sending SIGINT to process group {pgid}...")
    os.killpg(pgid, signal.SIGINT)

    try:
        out, err = proc.communicate(timeout=5)
        print("Server Output:\n", out)
        print("Server Errors:\n", err)
    except subprocess.TimeoutExpired:
        print("Parent process did not exit within timeout")

    print("Waiting for processes to exit...")
    for _ in range(10):
        time.sleep(1)
        still_alive = get_related_processes(pgid)
        if not still_alive:
            break

    lingering = get_related_processes(pgid)
    if lingering:
        pids = [p.pid for p in lingering]
        print(f"FAILED: Processes still alive in group {pgid}: {pids}")
        for p in lingering:
            try:
                p.kill()
            except Exception:
                pass
        assert False, f"Zombie processes detected in PGID {pgid} after SIGINT: {pids}"

    print("PASSED: Entire process group exited cleanly.")


if __name__ == "__main__":
    test_server_interrupt_full_cleanup()
