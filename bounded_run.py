#!/usr/bin/env python3
"""Run one finite bar helper with a whole deadline and end its whole process group.

    python3 -I bounded_run.py [--report-start] SECONDS -- PROGRAM [ARG ...]

The bar widget (BarWidget.qml) starts every finite helper (menu, bar state, store setup, mode
sync, autostart, theme/wallpaper runs, tiling) through this supervisor. The program runs in a
new session, so it and everything it starts share one process group of their own. That group is
ended when

* the program exits (success or error): whatever it left behind in the group,
* the deadline passes, or
* the supervisor itself is asked to stop (SIGTERM/SIGINT/SIGHUP, e.g. the shell's Process
  ``running = false`` at the bar's outer deadline),

each time with SIGTERM to the group, a grace period, then SIGKILL to the members still left.
The program is not reaped before the group has been signalled, so its pid (the group id)
cannot be reused meanwhile.

If the supervisor itself dies, even by SIGKILL (Quickshell kills a running Process when the
shell exits), a small watchdog ends the group at once. It is forked *before* the program starts
(no watchdog, no program), sits in a session of its own, and gets from the program itself, between
fork and exec, a pidfd of the program (SCM_RIGHTS), so it holds the program's identity even if the
supervisor dies right after the start. It then waits on a pipe whose only writer is the
supervisor. When that pipe closes without the supervisor's "done", it sends SIGTERM to the whole
group through that pidfd (PIDFD_SIGNAL_PROCESS_GROUP, Linux 6.9+: the group is the program's own,
never a later group that reused the number; it reaches the members also after the program
itself exited and was reaped, since the kernel keeps the group's identity while any member is
left), waits the grace period, then sends SIGKILL the same way. On an older kernel it falls back to the group number only while the program itself is still
unreaped (checked through the same pidfd). The program also gets SIGTERM directly
(PR_SET_PDEATHSIG).

With ``--report-start`` the supervisor prints the line ``bounded_run: started`` on stdout once
the program has really been started (exec succeeded), so the caller can tell a started helper
from one that could not start (exit 125, no line).

Long-lived things the helpers start on purpose (apps through ``uwsm-app`` or ``gio launch``,
notifications, the picker preload, ``omarchy theme set`` with its own deadline) are started in
sessions of their own, so they are not members of this group and are left alone.

Exit status: the program's own, 124 when the deadline passed, 143 when the supervisor was
stopped, 125 for a usage error or a program that could not start.
"""
from __future__ import annotations

import ctypes
import os
import select
import signal
import sys
import time

GRACE = 5.0          # seconds between SIGTERM and SIGKILL to the group
MAX_SECONDS = 3600.0
PR_SET_PDEATHSIG = 1
PIDFD_SIGNAL_PROCESS_GROUP = 4   # linux/pidfd.h, Linux 6.9+
STARTED_LINE = "bounded_run: started"

_stop_requested = False


def _request_stop(_signum, _frame):
    global _stop_requested
    _stop_requested = True


def group_members(pgid: int, exclude: int | None = None) -> list[int]:
    """Live (non-zombie) processes whose process group is pgid, read from /proc."""
    found = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == exclude:
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                data = handle.read(4096)
        except OSError:
            continue
        # comm may contain spaces and parentheses: the fields follow the last ")".
        fields = data[data.rfind(b")") + 2:].split()
        if len(fields) < 3:
            continue
        state, pgrp = fields[0], fields[2]
        if state in (b"Z", b"X"):
            continue
        try:
            if int(pgrp) == pgid:
                found.append(pid)
        except ValueError:
            continue
    return found


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def end_group(pgid: int, grace: float = GRACE) -> None:
    """SIGTERM the group, wait up to ``grace`` for it to empty, SIGKILL what is left.

    The leader (pid == pgid) must not be reaped yet, so the group id stays taken."""
    _killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        members = group_members(pgid)
        if not members:
            return
        time.sleep(0.05)
    _killpg(pgid, signal.SIGKILL)


def _read_exact(fd: int, size: int) -> bytes:
    data = b""
    while len(data) < size:
        try:
            chunk = os.read(fd, size - len(data))
        except InterruptedError:
            continue
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    return data


def _pidfd_pid(pidfd: int) -> int | None:
    """The pid a pidfd refers to, from /proc/self/fdinfo."""
    try:
        with open(f"/proc/self/fdinfo/{pidfd}", "rb") as handle:
            for line in handle.read(4096).splitlines():
                if line.startswith(b"Pid:"):
                    pid = int(line.split()[1])
                    return pid if pid > 0 else None
    except (OSError, ValueError, IndexError):
        pass
    return None


def watchdog(info_sock, alive_r: int, grace: float = GRACE) -> None:
    """The watchdog's work, armed before the program starts.

    info_sock: between fork and exec the program opens a pidfd of *itself* and passes it here
    (SCM_RIGHTS), so the watchdog holds the program's identity even if the supervisor dies right
    after the start, and a reused pid number can never stand in for it. alive_r: only the
    supervisor writes here; "d" means it ended the group itself, EOF without it means the
    supervisor died."""
    import socket
    try:
        _msg, fds, _flags, _addr = socket.recv_fds(info_sock, 1, 1)
    except OSError:
        fds = []
    info_sock.close()
    if len(fds) != 1:
        return                        # the program never started
    pidfd = fds[0]
    pgid = _pidfd_pid(pidfd)
    if pgid is None:
        return
    if _read_exact(alive_r, 1) == b"d":
        return
    if not _group_signal(pidfd, pgid, signal.SIGTERM):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not group_members(pgid):
            return
        time.sleep(0.05)
    _group_signal(pidfd, pgid, signal.SIGKILL)


def _group_signal(pidfd: int, pgid: int, sig: int) -> bool:
    """Signal the program's own process group through its pidfd. False once nothing is left."""
    try:
        signal.pidfd_send_signal(pidfd, sig, None, PIDFD_SIGNAL_PROCESS_GROUP)
        return True
    except ProcessLookupError:
        return False
    except (OSError, TypeError):
        pass
    # Kernel without process-group pidfd signals: the group number is still the program's own
    # only while the program itself (its leader, held by this pidfd) has not been reaped.
    try:
        signal.pidfd_send_signal(pidfd, 0)
    except OSError:
        return False
    _killpg(pgid, sig)
    return True


def _start_watchdog(grace: float):
    """Fork the watchdog before the program starts; returns (pid, info_sock, alive_w) or None."""
    import socket
    try:
        info_parent, info_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    except OSError:
        return None
    try:
        alive_r, alive_w = os.pipe()
    except OSError:
        info_parent.close()
        info_child.close()
        return None
    try:
        pid = os.fork()
    except OSError:
        info_parent.close()
        info_child.close()
        os.close(alive_r)
        os.close(alive_w)
        return None
    if pid == 0:   # the watchdog
        code = 0
        try:
            info_parent.close()
            os.close(alive_w)
            os.setsid()   # not a member of the program's group, nor of the caller's
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            watchdog(info_child, alive_r, grace)
        except BaseException:
            code = 1
        os._exit(code)
    info_child.close()
    os.close(alive_r)
    return pid, info_parent, alive_w


class _NotStarted(Exception):
    pass


def _child_setup(parent: int, info_sock):
    def setup():
        import socket
        # The program gets SIGTERM if the supervisor dies first (checked after setting it).
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        except Exception:
            pass
        if os.getppid() != parent:
            raise _NotStarted("supervisor gone")
        # Hand the watchdog a pidfd of this very process (close-on-exec, so the program never
        # holds it). Without it the watchdog could not end this group: do not start. Raising
        # here makes Popen fail (no exec, no start line).
        own = os.pidfd_open(os.getpid())
        try:
            if socket.send_fds(info_sock, [b"p"], [own]) != 1:
                raise _NotStarted("watchdog not told")
        finally:
            os.close(own)
    return setup


def _reap(pid: int) -> None:
    while True:
        try:
            os.waitpid(pid, 0)
            return
        except InterruptedError:
            continue
        except ChildProcessError:
            return


def _leader_exited(pid: int) -> bool:
    """True once the leader has exited; it is left unreaped (WNOWAIT)."""
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    return info is not None


def run(seconds: float, argv: list[str], grace: float = GRACE, report_start: bool = False) -> int:
    import subprocess

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _request_stop)
    # The watchdog is armed first: no program runs without one (fail closed).
    started = _start_watchdog(grace)
    if started is None:
        return 125
    watcher, info_sock, watch_w = started
    try:
        proc = subprocess.Popen(argv, start_new_session=True,
                                preexec_fn=_child_setup(os.getpid(), info_sock))
    except (OSError, ValueError, subprocess.SubprocessError):
        info_sock.close()
        os.close(watch_w)
        _reap(watcher)
        return 125
    info_sock.close()
    pgid = proc.pid
    deadline = time.monotonic() + seconds
    try:
        pidfd = os.pidfd_open(proc.pid)
    except OSError:
        pidfd = -1
    if report_start:
        try:
            print(STARTED_LINE, flush=True)
        except OSError:
            pass
    poller = select.poll()
    if pidfd >= 0:
        poller.register(pidfd, select.POLLIN)

    outcome = None   # "exited", "deadline" or "stopped"
    while outcome is None:
        if _stop_requested:
            outcome = "stopped"
            break
        if _leader_exited(proc.pid):
            outcome = "exited"
            break
        left = deadline - time.monotonic()
        if left <= 0:
            outcome = "deadline"
            break
        # A signal interrupts the wait (PEP 475 retries poll, so keep the slice short).
        wait_ms = int(min(left, 0.2) * 1000) + 1
        if pidfd >= 0:
            poller.poll(wait_ms)
        else:
            time.sleep(wait_ms / 1000)

    end_group(pgid, grace)
    try:
        os.write(watch_w, b"d")
    except OSError:
        pass
    os.close(watch_w)
    _reap(watcher)
    if pidfd >= 0:
        os.close(pidfd)
    code = proc.wait()
    if outcome == "deadline":
        return 124
    if outcome == "stopped":
        return 143
    return code if code >= 0 else 128 - code


def main(args: list[str]) -> int:
    report_start = bool(args) and args[0] == "--report-start"
    if report_start:
        args = args[1:]
    if len(args) < 3 or args[1] != "--":
        print("usage: bounded_run.py [--report-start] SECONDS -- PROGRAM [ARG ...]", file=sys.stderr)
        return 125
    try:
        seconds = float(args[0])
    except ValueError:
        return 125
    if not (0 < seconds <= MAX_SECONDS):
        return 125
    return run(seconds, args[2:], report_start=report_start)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
