"""bounded_run: the bar's finite helpers end as a whole process group (exit, deadline, stop),
and BarWidget.qml starts every finite helper through it."""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bounded_run  # noqa: E402

SUPERVISOR = str(ROOT / "bounded_run.py")

# A helper that leaves a child behind in its group: the child writes its pid, then sleeps.
# term_ignored: the child ignores SIGTERM, so only the SIGKILL after the grace ends it.
LEAVE_CHILD = r"""
import os, signal, subprocess, sys, time
pidfile, mode = sys.argv[1], sys.argv[2]
code = "import os,signal,sys,time\n"
if mode == "term_ignored":
    code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
code += "open(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(300)\n"
subprocess.Popen([sys.executable, "-c", code, pidfile], stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not os.path.exists(pidfile) or not open(pidfile).read():
    time.sleep(0.02)
if sys.argv[3] == "exit":
    sys.exit(3)
time.sleep(300)
"""


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    return data[data.rfind(b")") + 2:].split()[0] not in (b"Z", b"X")


class BoundedRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pidfile = os.path.join(self.tmp.name, "child.pid")
        self.leftover = None
        self.addCleanup(self.kill_leftover)

    def kill_leftover(self):
        try:
            pid = int(Path(self.pidfile).read_text())
        except (OSError, ValueError):
            return
        if alive(pid):
            os.kill(pid, signal.SIGKILL)

    def start(self, seconds, mode, end, grace=None):
        argv = [sys.executable, "-I", SUPERVISOR, str(seconds), "--",
                sys.executable, "-c", LEAVE_CHILD, self.pidfile, mode, end]
        env = dict(os.environ)
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def child_pid(self, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                text = Path(self.pidfile).read_text()
                if text:
                    return int(text)
            except OSError:
                pass
            time.sleep(0.02)
        self.fail("the helper's child never started")

    def wait_gone(self, pid, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not alive(pid):
                return True
            time.sleep(0.05)
        return False

    def test_a_finished_helper_takes_what_it_left_behind_with_it(self):
        proc = self.start(60, "plain", "exit")
        child = self.child_pid()
        self.assertEqual(proc.wait(timeout=15), 3)           # the helper's own status
        self.assertTrue(self.wait_gone(child, 2))

    def test_the_deadline_ends_the_whole_group(self):
        proc = self.start(1, "plain", "stay")
        child = self.child_pid()
        self.assertEqual(proc.wait(timeout=15), 124)
        self.assertTrue(self.wait_gone(child, 2))

    def test_sigterm_to_the_supervisor_ends_the_group_with_sigkill_after_the_grace(self):
        # What the bar's outer deadline does: Process running = false sends SIGTERM to the
        # supervisor only. A member that ignores SIGTERM still ends (SIGKILL after the grace).
        proc = self.start(60, "term_ignored", "stay")
        child = self.child_pid()
        time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=bounded_run.GRACE + 10), 143)
        self.assertTrue(self.wait_gone(child, 2))

    def test_the_helper_runs_in_a_group_of_its_own(self):
        proc = self.start(60, "plain", "stay")
        child = self.child_pid()
        self.assertNotEqual(os.getpgid(child), os.getpgid(0))
        self.assertNotEqual(os.getpgid(child), proc.pid)       # not the supervisor's group
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)

    def test_something_started_in_its_own_session_is_left_alone(self):
        # Apps (uwsm-app, gio launch), notifications and `omarchy theme set` start like this.
        code = ("import subprocess,sys;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'],"
                "start_new_session=True);open(sys.argv[1],'w').write(str(p.pid))")
        proc = subprocess.run([sys.executable, "-I", SUPERVISOR, "30", "--",
                               sys.executable, "-c", code, self.pidfile], timeout=20)
        self.assertEqual(proc.returncode, 0)
        pid = self.child_pid()
        self.assertTrue(alive(pid))

    def test_group_signal_reaches_members_after_the_leader_was_reaped(self):
        # The case of a supervisor that died: the leader exits and init reaps it, its child
        # (ignoring SIGTERM) stays. The pidfd group signal still reaches that child.
        code = ("import subprocess,sys;p=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)']);"
                "print(p.pid,flush=True)")
        leader = subprocess.Popen([sys.executable, "-c", code], start_new_session=True,
                                  stdout=subprocess.PIPE, text=True)
        pidfd = os.pidfd_open(leader.pid)
        self.addCleanup(os.close, pidfd)
        child = int(leader.stdout.readline())
        leader.stdout.close()
        leader.wait()                                   # leader reaped
        self.addCleanup(lambda: alive(child) and os.kill(child, signal.SIGKILL))
        self.assertTrue(bounded_run._group_signal(pidfd, leader.pid, signal.SIGKILL))
        self.assertTrue(self.wait_gone(child, 3))
        self.assertFalse(bounded_run._group_signal(pidfd, leader.pid, signal.SIGTERM))  # empty

    def test_usage_errors_start_nothing(self):
        for args in ([], ["5"], ["5", "x", "true"], ["0", "--", "true"], ["nan", "--", "true"],
                     ["99999", "--", "true"], ["abc", "--", "true"]):
            self.assertEqual(bounded_run.main(args), 125, args)

    def test_a_program_that_cannot_start(self):
        result = subprocess.run([sys.executable, "-I", SUPERVISOR, "5", "--",
                                 os.path.join(self.tmp.name, "missing")], timeout=10)
        self.assertEqual(result.returncode, 125)

    def test_no_watchdog_no_program(self):
        marker = os.path.join(self.tmp.name, "ran")
        original = bounded_run._start_watchdog
        bounded_run._start_watchdog = lambda grace: None
        try:
            code = bounded_run.run(5, ["/usr/bin/touch", marker])
        finally:
            bounded_run._start_watchdog = original
        self.assertEqual(code, 125)
        self.assertFalse(os.path.exists(marker))

    def test_a_watchdog_that_cannot_be_told_means_no_start_and_no_start_line(self):
        import contextlib, io
        marker = os.path.join(self.tmp.name, "ran")
        original = bounded_run._child_setup

        def failing(parent, sock):
            def setup():
                raise bounded_run._NotStarted("watchdog not told")
            return setup
        bounded_run._child_setup = failing
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = bounded_run.run(5, ["/usr/bin/touch", marker], report_start=True)
        finally:
            bounded_run._child_setup = original
        self.assertEqual(code, 125)
        self.assertFalse(os.path.exists(marker))
        self.assertNotIn(bounded_run.STARTED_LINE, out.getvalue())

    def test_the_start_line_comes_only_once_the_program_really_started(self):
        missing = subprocess.run([sys.executable, "-I", SUPERVISOR, "--report-start", "5", "--",
                                  os.path.join(self.tmp.name, "missing")],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(missing.returncode, 125)
        self.assertNotIn(bounded_run.STARTED_LINE, missing.stdout)
        ok = subprocess.run([sys.executable, "-I", SUPERVISOR, "--report-start", "5", "--", "true"],
                            capture_output=True, text=True, timeout=10)
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stdout.splitlines()[0], bounded_run.STARTED_LINE)

    def test_sigkill_to_the_supervisor_still_ends_the_group(self):
        # Quickshell kills a running Process when the shell exits: the watchdog ends the group.
        proc = self.start(60, "term_ignored", "exit")    # leader exits 3, child stays (ignores TERM)
        child = self.child_pid()
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        self.assertTrue(self.wait_gone(child, bounded_run.GRACE + 5))

    def test_sigkill_to_the_supervisor_while_the_leader_runs(self):
        proc = self.start(60, "plain", "stay")
        child = self.child_pid()
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        self.assertTrue(self.wait_gone(child, bounded_run.GRACE + 5))


class BarWidgetUsesTheSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.qml = (ROOT / "BarWidget.qml").read_text()

    def test_no_helper_runs_under_foreground_timeout_any_more(self):
        self.assertNotIn("--foreground", self.qml)
        self.assertIn('root.boundedPath, String(seconds), "--"', self.qml)

    def test_every_finite_helper_is_bounded(self):
        # Each python helper the widget runs goes through bounded(); none is started bare.
        for line in self.qml.splitlines():
            if '"/usr/bin/python3"' in line and "boundedPath" not in line:
                self.assertIn("root.bounded(", line, line.strip())

    def test_outer_deadlines_leave_room_for_the_supervisor_and_its_grace(self):
        grace = bounded_run.GRACE
        helper_limit = int(re.search(r"helperLimit: (\d+)", self.qml).group(1))
        tiling_limit = int(re.search(r"tilingLimit: (\d+)", self.qml).group(1))
        helper_outer = int(re.search(r"id: helperDeadline\s+interval: (\d+)", self.qml).group(1))
        tiling_outer = int(re.search(r"id: tilingDeadline\s+interval: (\d+)", self.qml).group(1))
        self.assertGreater(helper_outer / 1000, helper_limit + grace)
        self.assertGreater(tiling_outer / 1000, tiling_limit + grace)
        self.assertGreater(helper_limit, 120 + 60)     # theme switch 120 s after a 60 s lock wait

    def test_a_window_counts_as_handed_over_only_once_its_helper_started(self):
        start = self.qml.index("function nextTilingWindow()")
        body = self.qml[start:self.qml.index("property bool tilingBusy", start)]
        self.assertNotIn("markTilingSeen", body)
        self.assertNotIn("onStarted: root.tilingHelperStarted()", self.qml)   # supervisor only
        self.assertIn('"--report-start", String(tilingLimit), "--"', self.qml)
        self.assertIn('data === "%s") root.tilingHelperStarted()' % bounded_run.STARTED_LINE, self.qml)
        started = self.qml[self.qml.index("function tilingHelperStarted()"):]
        self.assertIn("markTilingSeen(tilingCurrent)", started[:300])
        settled = self.qml[self.qml.index("function tilingSettled()"):]
        settled = settled[:settled.index("\n  }\n")]
        self.assertIn("if (!tilingStarted)", settled)
        self.assertIn("tilingRetry.restart()", settled)


if __name__ == "__main__":
    unittest.main()
