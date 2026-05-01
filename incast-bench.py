#!/usr/bin/env python3
"""
incast-bench.py — Many-sender → one-receiver RoCEv2 incast orchestrator.

Drives an `ib_write_bw` incast experiment across a set of Mellanox
BlueField-3 / ConnectX hosts, snapshots NIC counters before and after, and
produces a Markdown report. Single-file, stdlib-only; macOS- and
Linux-friendly.

Defaults assume a fleet using `mlx5_0` / `data1` and the `rigi-bluefield`
QoS wrapper on BF3 hosts; both can be adjusted at the top of the file or
via CLI flags. See README.md for setup and caveats.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# =============================================================================
# Constants
# =============================================================================

# Identify BlueField-3 hosts by hostname allowlist rather than by SSH-probing.
# Probing would mean an extra round-trip per host on startup just to read
# /sys/class/infiniband/mlx5_0/board_id or similar — a hardcoded list keeps
# startup fast for stable fleets. Override at the CLI with --bluefield-hosts.
DEFAULT_BLUEFIELD_HOSTS = ("alveo-u50d-01", "alveo-u50d-02")

DEFAULT_RDMA_DEV = "mlx5_0"
DEFAULT_NET_IF = "data1"

# Counters worth a top-level table even when zero — these are the ones that
# directly indicate whether DCQCN signals (CNPs, ECN marks) are flowing and
# whether the network is dropping or pausing. Worth eyeballing every run
# rather than relying on "no errors visible" to mean things are working.
HEADLINE_COUNTERS = (
    ("hw", "np_cnp_sent"),
    ("hw", "rp_cnp_handled"),
    ("hw", "np_ecn_marked_roce_packets"),
    ("hw", "out_of_buffer"),
    ("hw", "out_of_sequence"),
    ("hw", "packet_seq_err"),
    ("hw", "local_ack_timeout_err"),
    ("hw", "implied_nak_seq_err"),
    ("hw", "rnr_nak_retry_err"),
    ("ethtool", "rx_ecn_mark"),
    ("ethtool", "rx_discards_phy"),
    ("ethtool", "rx_prio0_pause"),
    ("ethtool", "rx_prio3_pause"),
    ("ethtool", "tx_prio0_pause"),
    ("ethtool", "tx_prio3_pause"),
    ("ib", "port_rcv_errors"),
    ("ib", "port_xmit_discards"),
    ("ib", "port_xmit_data"),
    ("ib", "port_rcv_data"),
)

SSH_CONNECT_TIMEOUT = 10
RECEIVER_BIND_DELAY = 4    # 2s is too tight under load; 4s is a safe floor
HANDSHAKE_PAD = 30         # extra slack on top of -D for QP setup + teardown

# rigi-bluefield baseline. `set tc-mapping` and `set buffer` are intentionally
# omitted: they require a newer `mlnx_qos` than is installed on many fleets
# (depends on the `--tc_tsa` flag), and the default prio→TC mapping is
# typically already correct so they were no-ops anyway. Add them here if your
# fleet needs them.
RIGI_BASELINE_CMDS = (
    "sudo rigi-bluefield set pfc-prio3",
    "sudo rigi-bluefield set trust-dscp",
    "sudo rigi-bluefield set ecn-np --all",
    "sudo rigi-bluefield set ecn-rp --all",
)
# PCC init runs only if `doca_pcc` is actually running on the host (auto-
# detected via pgrep — see phase_baseline). Without the daemon, both
# `set pcc` and `pcc-query` error with "Bad Device" / "No such file"
# because pcc_counters.sh reads a diag-counter region that the daemon
# registers with the firmware at startup. We track per-host whether init
# succeeded so subsequent `pcc-query` calls only run on hosts that have
# both the daemon AND a working init.
RIGI_PCC_INIT = "sudo rigi-bluefield set pcc"
RIGI_PCC_QUERY = "sudo rigi-bluefield pcc-query"


# =============================================================================
# Output helpers
# =============================================================================

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(text, code):
    return f"\x1b[{code}m{text}\x1b[0m" if USE_COLOR else str(text)


def red(t):    return _c(t, "31")
def green(t):  return _c(t, "32")
def yellow(t): return _c(t, "33")
def cyan(t):   return _c(t, "36")
def bold(t):   return _c(t, "1")
def dim(t):    return _c(t, "2")


def status_ok(name, msg, width):
    print(f"  {name:<{width}}  {green('✓')} {msg}")


def status_warn(name, msg, width):
    print(f"  {name:<{width}}  {yellow('!')} {msg}")


def status_fail(name, msg, width):
    print(f"  {name:<{width}}  {red('✗')} {msg}")


def print_phase(num, total, title):
    print(f"\n{bold(f'[{num}/{total}]')} {title}")


# =============================================================================
# SSH plumbing
# =============================================================================

def _ssh_ctrl_dir() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    p = Path(cache) / "incast-bench" / "ssh-ctrl"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _strip_motd(text: str) -> str:
    """OpenSSH writes the remote MOTD to stderr alongside any real error.
    On auth failure the genuine reason gets buried under several hundred
    lines of welcome banner; show only lines that look like diagnostics."""
    keep = re.compile(
        r"(Permission|denied|host key|Could not|refused|@[\w.-]+|"
        r"port \d+:|not known|closed by remote|Bad config|"
        r"Authentication|fatal|kex_exchange|Connection)",
        re.I,
    )
    lines = [ln for ln in text.splitlines() if keep.search(ln)]
    return "\n".join(lines).strip() or text.strip()


class SSHError(Exception):
    def __init__(self, host, msg):
        self.host = host
        self.msg = msg
        super().__init__(f"{host}: {msg}")


@dataclass
class SSHTarget:
    host: str
    user: str
    ctrl_path: Path

    def _base(self, tty=False):
        args = [
            "ssh",
            "-o", f"ControlPath={self.ctrl_path}",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        ]
        if tty:
            # -tt forces TTY allocation even when our local stdin isn't a TTY
            # (which it won't be — we're running in a script). Required for
            # `sudo` on hosts where sudoers has `Defaults requiretty` set;
            # without -tt sudo fails with "sorry, you must have a tty to run
            # sudo" and the rigi-bluefield baseline never applies.
            args.append("-tt")
        args.append(f"{self.user}@{self.host}")
        return args

    def open_master(self):
        # ControlMaster lets every subsequent ssh invocation multiplex over
        # one TCP+auth handshake. ControlPersist=60s keeps the master alive
        # after the backgrounded process exits — without this we'd open
        # 20+ connections per run and hosts would (rightly) start
        # rate-limiting auth attempts.
        cmd = [
            "ssh",
            "-o", f"ControlPath={self.ctrl_path}",
            "-o", "ControlMaster=yes",
            "-o", "ControlPersist=60",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
            "-N", "-f",
            f"{self.user}@{self.host}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SSHError(self.host, _strip_motd(r.stderr))
        # Verify the master actually works. BatchMode=yes + missing
        # ssh-agent key often manifests here, not in -N -f.
        v = subprocess.run(self._base() + ["true"], capture_output=True, text=True)
        if v.returncode != 0:
            raise SSHError(self.host, _strip_motd(v.stderr))

    def close_master(self):
        cmd = [
            "ssh",
            "-o", f"ControlPath={self.ctrl_path}",
            "-O", "exit",
            f"{self.user}@{self.host}",
        ]
        subprocess.run(cmd, capture_output=True, text=True)

    def run(self, cmd: str, timeout: float = 30, tty: bool = False):
        result = subprocess.run(
            self._base(tty=tty) + [cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        # With -tt, the remote PTY echoes \r\n instead of \n and may also
        # inject standalone \r. Normalize so downstream parsers (which
        # are line-anchored regexes) don't trip.
        if tty:
            result.stdout = result.stdout.replace("\r\n", "\n").replace("\r", "")
            result.stderr = result.stderr.replace("\r\n", "\n").replace("\r", "")
        return result

    def popen(self, cmd: str) -> subprocess.Popen:
        return subprocess.Popen(
            self._base() + [cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )


# =============================================================================
# Host model
# =============================================================================

@dataclass
class Host:
    name: str
    user: str
    is_bluefield: bool
    role: str   # "receiver" | "sender"
    ssh: Optional[SSHTarget] = None
    log_path: Optional[Path] = None
    pcc_available: bool = False
    pre_counters: dict = field(default_factory=dict)
    post_counters: dict = field(default_factory=dict)
    pre_pcc_raw: str = ""
    post_pcc_raw: str = ""
    perftest_results: list = field(default_factory=list)

    def log_append(self, text: str):
        with open(self.log_path, "a") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    def log_section(self, title: str):
        self.log_append(
            f"\n========== {title} @ {datetime.now().isoformat(timespec='seconds')} =========="
        )


# =============================================================================
# Counter snapshots
# =============================================================================

# All three counter sources in one SSH round-trip per host. Mellanox HW
# counters are read-only monotonic registers — we snapshot before and after
# and subtract. Don't try to reset (would need a driver reload).
COUNTER_SCRIPT = (
    r"""
echo '===ETHTOOL==='
ethtool -S """ + DEFAULT_NET_IF + r""" 2>&1 || true
echo '===IB==='
for f in /sys/class/infiniband/""" + DEFAULT_RDMA_DEV + r"""/ports/1/counters/*; do
    [ -f "$f" ] && printf '%s:%s\n' "$(basename $f)" "$(cat $f 2>/dev/null)"
done
echo '===HW==='
for f in /sys/class/infiniband/""" + DEFAULT_RDMA_DEV + r"""/ports/1/hw_counters/*; do
    [ -f "$f" ] && printf '%s:%s\n' "$(basename $f)" "$(cat $f 2>/dev/null)"
done
"""
)

_ETHTOOL_RE = re.compile(r"^\s*([\w]+):\s*(-?\d+)\s*$")
_SYSFS_RE = re.compile(r"^([\w]+):(-?\d+)\s*$")
_PCC_RE = re.compile(r"^\s*([\w_.-]+)\s*[:=]\s*(-?\d+)\s*$")


def _parse_counter_output(text):
    counters = {}
    section = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("==="):
            section = line.strip("= ").strip()
            continue
        if section == "ETHTOOL":
            m = _ETHTOOL_RE.match(line)
            if m:
                counters[("ethtool", m.group(1))] = int(m.group(2))
        elif section in ("IB", "HW"):
            m = _SYSFS_RE.match(line)
            if m:
                cls = "ib" if section == "IB" else "hw"
                counters[(cls, m.group(1))] = int(m.group(2))
        elif section == "PCC":
            m = _PCC_RE.match(line)
            if m:
                counters[("pcc", m.group(1))] = int(m.group(2))
    return counters


def _extract_section(raw: str, wanted: str) -> str:
    out, in_section = [], False
    for line in raw.splitlines():
        if line.startswith("==="):
            in_section = wanted in line
            continue
        if in_section:
            out.append(line)
    return "\n".join(out)


def snapshot_host(host: Host):
    cmd = COUNTER_SCRIPT
    use_tty = False
    if host.is_bluefield and host.pcc_available:
        cmd += f"\necho '===PCC==='\n{RIGI_PCC_QUERY} 2>&1 || true\n"
        # pcc-query is invoked via sudo and the fleet's sudoers has
        # `Defaults requiretty`; without -tt the whole script gets
        # rejected with "sorry, you must have a tty to run sudo".
        use_tty = True
    res = host.ssh.run(cmd, timeout=45, tty=use_tty)
    return _parse_counter_output(res.stdout), res.stdout


def diff_counters(pre: dict, post: dict) -> dict:
    out = {}
    for k in set(pre) | set(post):
        delta = post.get(k, 0) - pre.get(k, 0)
        if delta != 0:
            out[k] = delta
    return out


# =============================================================================
# perftest
# =============================================================================

def perftest_cmd(port, msg_size, duration, gid_idx, tos, target=None):
    """Build an `ib_write_bw` invocation.

    The non-obvious bits (the traffic-priority trap):
      -R         use rdma_cm for QP setup (required for --tos to apply)
      --tos 105  = (DSCP 26 << 2) | 0b01 = ECT(1).  DSCP 26 maps to prio 3
                  via DSCP-trust on the BF3 hosts; ECT(1) makes packets
                  ECN-capable so the switch can MARK on congestion instead
                  of dropping. --tos 104 looks right but its low 2 bits are
                  Not-ECT and the switch will drop, not mark.
    """
    parts = [
        "ib_write_bw",
        "-d", DEFAULT_RDMA_DEV,
        "-p", str(port),
        "-x", str(gid_idx),
        "-F",                  # don't complain about CPU frequency scaling
        "-R",                  # rdma_cm
        "--tos", str(tos),
        "--report_gbits",
        "-D", str(duration),
        "-s", str(msg_size),
    ]
    if target is not None:
        parts.append(target)
    return " ".join(shlex.quote(p) for p in parts)


_BW_HEADER_RE = re.compile(r"BW peak.*BW average.*MsgRate", re.I)


def parse_perftest(text: str):
    """Pull the summary line out of `ib_write_bw` output.

    Format:
        #bytes  #iterations   BW peak[Gb/sec]  BW average[Gb/sec]  MsgRate[Mpps]
    The data line is the next non-separator non-empty line after the header.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _BW_HEADER_RE.search(line):
            for j in range(i + 1, len(lines)):
                cand = lines[j].strip()
                if not cand or set(cand) <= set("- "):
                    continue
                f = cand.split()
                if len(f) >= 5:
                    try:
                        return {
                            "bytes": int(f[0]),
                            "bw_peak": float(f[2]),
                            "bw_avg": float(f[3]),
                            "mpps": float(f[4]),
                        }
                    except ValueError:
                        return None
                return None
    return None


def spawn_perftest(host: Host, role: str, port: int, args, target=None):
    cmd = perftest_cmd(port, args.msg_size, args.duration, args.gid_index, args.tos, target)
    proc = host.ssh.popen(cmd)
    out_buf: list = []
    prefix = f"[{role} :{port}] "
    log_path = host.log_path

    def reader():
        with open(log_path, "a") as f:
            f.write(
                f"\n========== ib_write_bw {role} :{port} @ "
                f"{datetime.now().isoformat(timespec='seconds')} ==========\n"
            )
            f.write(f"$ {cmd}\n")
            for line in proc.stdout:
                out_buf.append(line)
                f.write(prefix + line)
                f.flush()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return {"proc": proc, "thread": t, "out": out_buf, "port": port,
            "role": role, "host": host, "cmd": cmd}


# =============================================================================
# iTerm2 visibility
# =============================================================================

def has_iterm2() -> bool:
    if sys.platform != "darwin":
        return False
    return os.path.exists("/Applications/iTerm.app") or os.path.exists(
        os.path.expanduser("~/Applications/iTerm.app")
    )


def open_iterm2_window(log_path: Path, title: str):
    # Setting the iTerm window title is finicky because the displayed
    # title depends on the profile's "Title" setting. Setting `name` via
    # AppleScript only shows up when the profile includes "Session Name"
    # in its title format — for default profiles that show "Job Name"
    # (which is what was making every window read "tail -F"), we need
    # to also send OSC escape sequences from inside the shell:
    #   ESC]0;NAME BEL  → icon + window title (the standard one)
    #   ESC]1;NAME BEL  → icon name (which iTerm uses as tab title)
    #   ESC]2;NAME BEL  → window title (xterm-style)
    # All three are sent up front via printf, then tail runs. Belt-and-
    # suspenders: at least one of these will display depending on the
    # user's profile config.
    title_safe = title.replace("'", "'\\''")  # escape ' for single-quoted shell
    title_escapes = (
        r"\033]0;" + title_safe + r"\007"
        + r"\033]1;" + title_safe + r"\007"
        + r"\033]2;" + title_safe + r"\007"
    )
    shell_cmd = (
        f"printf '{title_escapes}'; "
        + "tail -F " + shlex.quote(str(log_path))
    )
    # AppleScript string-literal escapes: \ → \\, " → \"
    cmd_escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    title_escaped = title.replace("\\", "\\\\").replace('"', '\\"')
    # Capture the new window in a variable rather than relying on `current
    # window` (which races when multiple windows are created in quick
    # succession). Run `write text` BEFORE `set name` so the window is
    # populated even if name-setting fails on a future iTerm version.
    script = (
        'tell application "iTerm"\n'
        '    activate\n'
        '    set w to (create window with default profile)\n'
        '    tell current session of w\n'
        f'        write text "{cmd_escaped}"\n'
        f'        set name to "{title_escaped}"\n'
        '    end tell\n'
        'end tell\n'
    )
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        # Surface the error rather than silently leaving an empty window.
        sys.stderr.write(
            f"  {yellow('!')} osascript failed for {title}: "
            f"{(r.stderr or 'unknown').strip()}\n"
        )


# =============================================================================
# Phases
# =============================================================================

def phase_open_ssh(hosts):
    width = max(len(h.name) for h in hosts)
    errors = []

    def go(h):
        try:
            h.ssh.open_master()
            return h, None
        except SSHError as e:
            return h, e

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
        for fut in as_completed([ex.submit(go, h) for h in hosts]):
            h, err = fut.result()
            if err is None:
                status_ok(h.name, "connected", width)
            else:
                first_line = err.msg.split("\n")[0][:120] if err.msg else "unknown error"
                status_fail(h.name, first_line, width)
                errors.append(err)

    if errors:
        # Lesson #2: if every host fails with "Permission denied", the
        # ssh-agent is almost certainly the culprit (laptop slept, agent
        # died, key timed out). Surface this as the very first thing to
        # try.
        msg = ["Could not establish SSH to all hosts."]
        if all(re.search(r"denied|permission", e.msg, re.I) for e in errors):
            msg.append(
                "All failures look like 'Permission denied'. Check your "
                "ssh-agent first:\n"
                "  $ ssh-add -l        # should list a key\n"
                "  $ ssh-add ~/.ssh/id_ed25519   # if empty"
            )
        raise RuntimeError("\n".join(msg))


def phase_pkill_stale(hosts, label):
    """Kill any orphaned ib_write_bw from a previous crashed run.

    Lesson #1: receiver perftests outlive their parent SSH session if the
    previous run was Ctrl+C'd, lost SSH, or hit a timeout — the next run
    then hits EADDRINUSE on port 18515. Pkill on every host before AND
    after every run (the after part lives in `finally`, BEFORE
    ControlMaster teardown — otherwise pkill never reaches the host).
    """
    width = max(len(h.name) for h in hosts)

    def go(h):
        # `pkill -u $USER` returns 1 when no processes match — fine.
        # The double-pkill (TERM, then KILL after a brief grace) is so we
        # don't leave anything zombied if a perftest was wedged.
        h.ssh.run(
            "pkill -u $USER ib_write_bw; sleep 0.3; "
            "pkill -9 -u $USER ib_write_bw; true",
            timeout=15,
        )
        return h

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
        for fut in as_completed([ex.submit(go, h) for h in hosts]):
            h = fut.result()
            status_ok(h.name, label, width)


def phase_visibility(hosts, mode_arg):
    if mode_arg == "iterm2" and not has_iterm2():
        print(f"  {yellow('!')} iTerm2 requested but not installed; falling back to interleaved")
        return "interleaved"
    if mode_arg == "auto":
        mode = "iterm2" if has_iterm2() else "interleaved"
    else:
        mode = mode_arg

    if mode == "iterm2":
        for h in hosts:
            open_iterm2_window(h.log_path, h.name)
            # Stagger so iTerm doesn't drop one of them under contention.
            # 0.15s was too tight on a busy laptop and would occasionally
            # cause windows to merge or the AppleScript to fail silently.
            time.sleep(0.3)
        print(f"  {green('✓')} {len(hosts)} iTerm2 window(s) opened")
    else:
        print(f"  {dim('(interleaved mode — host output streams below the run line)')}")
    return mode


def _trim(s: str, n: int = 200) -> str:
    s = s.strip()
    return s[:n] + "…" if len(s) > n else s


def phase_baseline(hosts, failed):
    width = max(len(h.name) for h in hosts)

    def go(h):
        if not h.is_bluefield:
            return h, "skipped (ConnectX)", []
        h.log_section("BASELINE")
        host_failures = []

        # Preflight: ensure Mellanox Software Tools is running. `mst start`
        # loads the kernel module and creates /dev/mst/mt41692_pciconf0,
        # which rigi-bluefield's pcc_counters.sh and mlxreg-based commands
        # need. Typically persists across reboots once started, so this is
        # a no-op on subsequent runs.
        mst_check = h.ssh.run("ls /dev/mst/mt*_pciconf0 2>/dev/null", timeout=10)
        if mst_check.returncode != 0 or not mst_check.stdout.strip():
            r = h.ssh.run("sudo mst start", timeout=20, tty=True)
            h.log_append(f"$ sudo mst start\n{r.stdout}{r.stderr}\nrc={r.returncode}")
            if r.returncode != 0:
                # If mst isn't installed at all, surface that clearly —
                # everything else on this host will fail without it.
                host_failures.append(("sudo mst start", _trim(r.stderr or r.stdout)))
                return h, f"{red('mst start failed')} (skipping baseline)", host_failures

        for cmd in RIGI_BASELINE_CMDS:
            # tty=True: every rigi-bluefield call goes through sudo, and
            # our hosts have `Defaults requiretty` in sudoers. Without -tt
            # every command fails with "sorry, you must have a tty to run
            # sudo" before it does anything.
            r = h.ssh.run(cmd, timeout=30, tty=True)
            h.log_append(f"$ {cmd}\n{r.stdout}{r.stderr}\nrc={r.returncode}")
            if r.returncode != 0:
                host_failures.append((cmd, _trim(r.stderr or r.stdout)))

        # PCC ops only make sense when a `doca_pcc` daemon is actually
        # running on this host. pcc_counters.sh reads/writes a diag-counter
        # region that the daemon registers with the firmware at startup —
        # without the daemon, both `set pcc` and `pcc-query` error with
        # "Bad Device" / "No such file" and pollute the failed-commands
        # table on every run. Detect via `pgrep` and skip cleanly when
        # absent (typical on the receiver host, or any baseline run
        # without a PCC algorithm loaded).
        pcc_probe = h.ssh.run("pgrep -x doca_pcc", timeout=10)
        if pcc_probe.returncode != 0:
            h.log_append(
                "$ pgrep -x doca_pcc\n"
                "(no daemon running on this host — skipping PCC init/query)"
            )
            return h, "configured (no PCC daemon)", host_failures

        # Daemon present — try to wire up the diag counters. If this fails
        # despite the daemon running, the firmware is in a bad state
        # (e.g. stale registration from a previous session); we surface
        # that as a real failure since PCC was expected to work.
        r = h.ssh.run(RIGI_PCC_INIT, timeout=30, tty=True)
        h.log_append(f"$ {RIGI_PCC_INIT}\n{r.stdout}{r.stderr}\nrc={r.returncode}")
        if r.returncode == 0:
            h.pcc_available = True
            return h, "configured + pcc ok", host_failures
        else:
            host_failures.append((RIGI_PCC_INIT, _trim(r.stderr or r.stdout)))
            return h, f"configured ({yellow('pcc init failed')})", host_failures

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
        for fut in as_completed([ex.submit(go, h) for h in hosts]):
            h, msg, hf = fut.result()
            for cmd, err in hf:
                failed.append((h.name, cmd, err))
            if "skipped" in msg:
                print(f"  {h.name:<{width}}  {dim('—')} {msg}")
            else:
                status_ok(h.name, msg, width)


def phase_snapshot(hosts, when, failed):
    width = max(len(h.name) for h in hosts)

    def go(h):
        h.log_section(f"SNAPSHOT {when.upper()}")
        try:
            counters, raw = snapshot_host(h)
            h.log_append(raw)
            return h, counters, raw, None
        except subprocess.TimeoutExpired as e:
            return h, {}, "", str(e)
        except Exception as e:
            return h, {}, "", f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
        for fut in as_completed([ex.submit(go, h) for h in hosts]):
            h, counters, raw, err = fut.result()
            if err:
                failed.append((h.name, f"snapshot ({when})", err))
                status_fail(h.name, f"snapshot failed: {err}", width)
                continue
            if when == "pre":
                h.pre_counters = counters
                h.pre_pcc_raw = _extract_section(raw, "PCC")
            else:
                h.post_counters = counters
                h.post_pcc_raw = _extract_section(raw, "PCC")
            status_ok(h.name, f"{len(counters)} counters", width)


def phase_run(receiver, senders, args, mode, failed):
    width = max(len(h.name) for h in [receiver] + senders)

    # One receiver per sender, on consecutive ports starting at base_port.
    print(f"  {dim('spawning receivers...')}")
    receiver_handles = []
    for i, _ in enumerate(senders):
        port = args.base_port + i
        receiver_handles.append(spawn_perftest(receiver, "recv", port, args, target=None))

    # 4s gives the receivers time to bind and start listening before any
    # sender tries to connect; 2s was empirically too tight under load.
    print(f"  {dim(f'waiting {RECEIVER_BIND_DELAY}s for receivers to bind...')}")
    time.sleep(RECEIVER_BIND_DELAY)

    # Fail-fast check: if a receiver already exited, the corresponding
    # sender will never connect. Surface it now so the user isn't waiting
    # 30s wondering what happened.
    for rh in receiver_handles:
        if rh["proc"].poll() is not None:
            failed.append((receiver.name, rh["cmd"],
                           "receiver exited before senders connected"))
            status_fail(receiver.name, f"receiver on :{rh['port']} died early", width)

    print(f"  {dim('firing senders...')}")
    target = f"{receiver.name}-{DEFAULT_NET_IF}"
    sender_handles = []
    for i, s in enumerate(senders):
        port = args.base_port + i
        sender_handles.append(spawn_perftest(s, "send", port, args, target=target))

    streamer_stop = None
    if mode == "interleaved":
        streamer_stop = _start_interleaved_streamer([receiver] + senders)

    # Wait for every subprocess with a generous cap.
    cap = args.duration + HANDSHAKE_PAD
    deadline = time.monotonic() + cap
    all_handles = sender_handles + receiver_handles
    for h in all_handles:
        remaining = max(0.5, deadline - time.monotonic())
        try:
            h["proc"].wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            h["proc"].kill()
            failed.append((h["host"].name, h["cmd"], f"perftest exceeded {cap}s"))
        h["thread"].join(timeout=2)

    if streamer_stop is not None:
        streamer_stop.set()
        # Brief moment to let final lines flush.
        time.sleep(0.3)

    # Parse summaries.
    print()  # spacing after streamed output
    for h in sender_handles + receiver_handles:
        text = "".join(h["out"])
        result = parse_perftest(text)
        if result is None:
            failed.append((h["host"].name, h["cmd"], "couldn't parse BW summary from output"))
            status_fail(h["host"].name, f"{h['role']} :{h['port']} no summary", width)
            continue
        result.update({"port": h["port"], "role": h["role"]})
        h["host"].perftest_results.append(result)
        status_ok(
            h["host"].name,
            f"{h['role']:<6} :{h['port']}  "
            f"avg {result['bw_avg']:>6.2f} Gb/s  "
            f"peak {result['bw_peak']:>6.2f} Gb/s  "
            f"{result['mpps']:>6.3f} Mpps",
            width,
        )


_INTERLEAVE_COLORS = ["31", "32", "33", "34", "35", "36"]


def _start_interleaved_streamer(hosts):
    """Tail each host's log file and prefix lines with a colored hostname."""
    stop = threading.Event()

    def tailer(h, code):
        try:
            f = open(h.log_path, "r")
        except FileNotFoundError:
            return
        f.seek(0, os.SEEK_END)
        prefix = _c(f"{h.name:>14}", code) + " │ "
        while not stop.is_set():
            line = f.readline()
            if not line:
                time.sleep(0.05)
                continue
            sys.stdout.write(prefix + line)
            sys.stdout.flush()
        f.close()

    for i, h in enumerate(hosts):
        t = threading.Thread(
            target=tailer,
            args=(h, _INTERLEAVE_COLORS[i % len(_INTERLEAVE_COLORS)]),
            daemon=True,
        )
        t.start()
    return stop


# =============================================================================
# Reporting
# =============================================================================

def _fmt_int(n):
    return f"{n:,}"


def _short(name):
    # Strip a common prefix to keep table columns narrow. Tweak or remove
    # if your hostnames don't share a prefix.
    return name.replace("alveo-", "")


def _ecn_label(tos):
    return {0: "Not-ECT", 1: "ECT(1)", 2: "ECT(0)", 3: "CE"}[tos & 0b11]


def write_report(path, receiver, senders, args, started_at, failed):
    hosts = [receiver] + senders
    L = []
    L.append("# Incast benchmark report")
    L.append("")
    L.append(f"- **Timestamp:** {started_at.isoformat(timespec='seconds')}")
    L.append(f"- **Receiver:** `{receiver.name}`")
    L.append(f"- **Senders:** {', '.join('`' + s.name + '`' for s in senders)}")
    L.append(
        f"- **Duration:** {args.duration}s   "
        f"**Message size:** {args.msg_size} B   "
        f"**GID idx:** {args.gid_index}   "
        f"**TOS:** {args.tos} (DSCP {args.tos >> 2}, {_ecn_label(args.tos)})"
    )
    L.append("")
    L.append("## Configuration")
    L.append("")
    L.append("```")
    L.append("$ " + " ".join(shlex.quote(a) for a in [Path(sys.argv[0]).name] + sys.argv[1:]))
    L.append("```")
    L.append("")

    # Throughput
    L.append("## Throughput")
    L.append("")
    L.append("| Host | Role | Port | BW avg [Gb/s] | BW peak [Gb/s] | MsgRate [Mpps] |")
    L.append("|---|---|---:|---:|---:|---:|")
    sender_total = 0.0
    receiver_total = 0.0
    for s in senders:
        for r in s.perftest_results:
            L.append(f"| `{s.name}` | sender | {r['port']} | "
                     f"{r['bw_avg']:.2f} | {r['bw_peak']:.2f} | {r['mpps']:.3f} |")
            sender_total += r["bw_avg"]
    for r in receiver.perftest_results:
        L.append(f"| `{receiver.name}` | receiver | {r['port']} | "
                 f"{r['bw_avg']:.2f} | {r['bw_peak']:.2f} | {r['mpps']:.3f} |")
        receiver_total += r["bw_avg"]
    L.append("")
    L.append(f"- **Aggregate sender BW:** {sender_total:.2f} Gb/s")
    L.append(f"- **Aggregate receiver BW:** {receiver_total:.2f} Gb/s")
    L.append("")

    # Headline counters
    L.append("## Headline counter deltas")
    L.append("")
    L.append("| Counter | " + " | ".join(_short(h.name) for h in hosts) + " |")
    L.append("|---|" + "|".join(["---:"] * len(hosts)) + "|")
    for cls, name in HEADLINE_COUNTERS:
        row = [f"`{cls}:{name}`"]
        for h in hosts:
            d = diff_counters(h.pre_counters, h.post_counters)
            v = d.get((cls, name), 0)
            row.append(_fmt_int(v) if v else "—")
        L.append("| " + " | ".join(row) + " |")
    L.append("")

    # Per-host details
    L.append("## Per-host details")
    L.append("")
    headline_set = set(HEADLINE_COUNTERS)
    for h in hosts:
        d = diff_counters(h.pre_counters, h.post_counters)
        non_headline = sorted(
            [(k, v) for k, v in d.items() if k not in headline_set],
            key=lambda kv: -abs(kv[1]),
        )
        flags = [h.role, "BlueField" if h.is_bluefield else "ConnectX"]
        if h.is_bluefield:
            flags.append("PCC ok" if h.pcc_available else "PCC failed")
        L.append("<details>")
        L.append(
            f"<summary><strong>{h.name}</strong> "
            f"({', '.join(flags)}) — {len(non_headline)} other non-zero deltas</summary>"
        )
        L.append("")
        if non_headline:
            L.append("| Counter | Delta |")
            L.append("|---|---:|")
            for (cls, name), v in non_headline:
                L.append(f"| `{cls}:{name}` | {_fmt_int(v)} |")
            L.append("")
        if h.pcc_available and h.post_pcc_raw.strip():
            L.append("**`rigi-bluefield pcc-query` (post):**")
            L.append("")
            L.append("```")
            L.append(h.post_pcc_raw.strip())
            L.append("```")
            L.append("")
        L.append("</details>")
        L.append("")

    # Failed commands
    L.append("## Failed commands")
    L.append("")
    if failed:
        L.append("| Host | Command | Error |")
        L.append("|---|---|---|")
        for host, cmd, err in failed:
            cmd_clean = cmd.replace("|", "\\|").replace("\n", " ").strip()
            err_clean = err.replace("|", "\\|").replace("\n", " ").strip()
            L.append(f"| `{host}` | `{cmd_clean}` | {err_clean} |")
    else:
        L.append("_None._")
    L.append("")

    path.write_text("\n".join(L))


def write_aggregate(path, run_dirs, per_run_metrics):
    L = ["# Incast benchmark — aggregate", ""]
    L.append(f"**Runs:** {len(run_dirs)}")
    L.append("")
    if per_run_metrics:
        keys = sorted({k for m in per_run_metrics for k in m.keys()})
        L.append("| Metric | Mean | Stddev | Min | Max |")
        L.append("|---|---:|---:|---:|---:|")
        for k in keys:
            vals = [m[k] for m in per_run_metrics if k in m]
            if not vals:
                continue
            mean = statistics.mean(vals)
            stdev = statistics.stdev(vals) if len(vals) >= 2 else 0.0
            L.append(f"| {k} | {mean:.2f} | {stdev:.2f} | {min(vals):.2f} | {max(vals):.2f} |")
        L.append("")
    L.append("## Per-run reports")
    L.append("")
    for d in run_dirs:
        L.append(f"- [{d.name}/report.md]({d.name}/report.md)")
    L.append("")
    path.write_text("\n".join(L))


# =============================================================================
# Terminal summary
# =============================================================================

def print_terminal_summary(receiver, senders, run_dir, failed):
    hosts = [receiver] + senders
    width = max(len(h.name) for h in hosts)

    print()
    print("=" * 72)
    print(f"  RESULTS — {receiver.name}  ({len(senders)} senders)")
    print("=" * 72)
    print()
    print(f"  {'Host':<{width}}  {'Role':<8}  {'Port':>5}  "
          f"{'BW avg Gb/s':>11}  {'BW peak Gb/s':>12}  {'Mpps':>6}")
    print(f"  {'-'*width}  {'-'*8}  {'-'*5}  {'-'*11}  {'-'*12}  {'-'*6}")
    sender_total = 0.0
    receiver_total = 0.0
    for s in senders:
        for r in s.perftest_results:
            print(f"  {s.name:<{width}}  {'sender':<8}  {r['port']:>5}  "
                  f"{r['bw_avg']:>11.2f}  {r['bw_peak']:>12.2f}  {r['mpps']:>6.3f}")
            sender_total += r["bw_avg"]
    for r in receiver.perftest_results:
        print(f"  {receiver.name:<{width}}  {'receiver':<8}  {r['port']:>5}  "
              f"{r['bw_avg']:>11.2f}  {r['bw_peak']:>12.2f}  {r['mpps']:>6.3f}")
        receiver_total += r["bw_avg"]
    print()
    print(f"  Aggregate sender BW:    {sender_total:.2f} Gb/s")
    print(f"  Aggregate receiver BW:  {receiver_total:.2f} Gb/s")
    print()
    print(f"  Non-zero headline counter deltas:")
    any_nonzero = False
    for cls, name in HEADLINE_COUNTERS:
        per_host = []
        for h in hosts:
            d = diff_counters(h.pre_counters, h.post_counters)
            v = d.get((cls, name), 0)
            if v:
                per_host.append(f"{_short(h.name)}={_fmt_int(v)}")
        if per_host:
            any_nonzero = True
            label = f"{cls}:{name}"
            print(f"    {label:<32}  {', '.join(per_host)}")
    if not any_nonzero:
        print(f"    {dim('(all zero — sanity-check your TOS / DSCP-trust setup)')}")
    print()
    if failed:
        print(f"  {yellow(f'{len(failed)} command(s) failed')} "
              f"— see report.md → 'Failed commands'")
        print()
    print(f"  Full report:  {run_dir / 'report.md'}")
    print(f"  Raw logs:     {run_dir}/")
    print("=" * 72)


# =============================================================================
# Main
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="RoCEv2 incast orchestrator for Mellanox BF3 / ConnectX fleets.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("-r", "--receiver", required=True, help="Receiver hostname")
    p.add_argument("-s", "--senders", required=True, nargs="+",
                   help="Sender hostnames (one or more)")
    p.add_argument("-D", "--duration", type=int, default=30,
                   help="ib_write_bw duration in seconds")
    p.add_argument("-S", "--msg-size", type=int, default=65536,
                   help="ib_write_bw message size in bytes")
    p.add_argument("-x", "--gid-index", type=int, default=3, help="RoCEv2 GID index")
    p.add_argument("--base-port", type=int, default=18515,
                   help="First TCP port; one per sender")
    p.add_argument("--runs", type=int, default=1,
                   help="Repeat N times for mean ± stddev (nested in run-NN/ subdirs)")
    p.add_argument("--user", default=os.environ.get("USER", "root"), help="SSH user")
    p.add_argument("--skip-baseline", action="store_true",
                   help="Skip rigi-bluefield baseline configuration")
    p.add_argument("--mode", choices=["auto", "iterm2", "interleaved"], default="auto",
                   help="Live-output mode")
    p.add_argument("--bluefield-hosts", default=",".join(DEFAULT_BLUEFIELD_HOSTS),
                   help="Comma-separated allowlist of BlueField-3 hosts")
    p.add_argument("--tos", type=int, default=105,
                   help="IP TOS for ib_write_bw -R --tos (default 105 = "
                        "DSCP 26 << 2 | ECT(1); see README)")
    return p.parse_args()


def run_once(args, bf_set, run_dir, started_at):
    receiver = Host(name=args.receiver, user=args.user,
                    is_bluefield=args.receiver in bf_set, role="receiver")
    senders = [Host(name=h, user=args.user, is_bluefield=h in bf_set, role="sender")
               for h in args.senders]
    hosts = [receiver] + senders

    ctrl_dir = _ssh_ctrl_dir()
    for h in hosts:
        h.ssh = SSHTarget(host=h.name, user=h.user, ctrl_path=ctrl_dir / h.name)
        h.log_path = run_dir / f"{h.name}.log"
        h.log_path.touch()

    failed = []
    interrupted = False

    try:
        print_phase(1, 7, "Opening SSH ControlMaster connections...")
        phase_open_ssh(hosts)

        print_phase(2, 7, "Cleaning up any stale ib_write_bw on all hosts...")
        phase_pkill_stale(hosts, label="cleared")

        print_phase(3, 7, "Opening visibility windows...")
        mode = phase_visibility(hosts, args.mode)

        print_phase(4, 7, "Applying baseline configuration on all hosts...")
        if args.skip_baseline:
            print(f"  {dim('skipped (--skip-baseline)')}")
        else:
            phase_baseline(hosts, failed)

        print_phase(5, 7, "Snapshotting counters (pre)...")
        phase_snapshot(hosts, "pre", failed)

        print_phase(6, 7,
                    f"Running benchmark: {len(senders)} senders → {receiver.name}, "
                    f"ports {args.base_port}..{args.base_port + len(senders) - 1}, "
                    f"{args.duration}s + handshake...")
        phase_run(receiver, senders, args, mode, failed)

        print_phase(7, 7, "Snapshotting counters (post)...")
        phase_snapshot(hosts, "post", failed)

    except KeyboardInterrupt:
        interrupted = True
        print(red("\n\nInterrupted; bailing without writing report."))
    except SSHError as e:
        # Don't double-print; phase_open_ssh already showed each host.
        print(red(f"\nFatal: {e}"))
        return 1, {}, None, [], failed
    except RuntimeError as e:
        print(red(f"\nFatal: {e}"))
        return 1, {}, None, [], failed
    except Exception as e:
        print(red(f"\nUnexpected: {type(e).__name__}: {e}"))
        return 1, {}, None, [], failed
    finally:
        # Critical cleanup ordering:
        #   1. pkill ib_write_bw on every host so receivers don't outlive
        #      their parent SSH session and block the listen ports next run.
        #   2. THEN tear down ControlMaster.
        # Reversing the order means the pkill never reaches the host.
        try:
            phase_pkill_stale(hosts, label="post-run cleanup")
        except Exception:
            pass
        for h in hosts:
            try:
                h.ssh.close_master()
            except Exception:
                pass

    if interrupted:
        return 130, {}, None, [], failed

    write_report(run_dir / "report.md", receiver, senders, args, started_at, failed)

    sender_total = sum(r["bw_avg"] for s in senders for r in s.perftest_results)
    receiver_total = sum(r["bw_avg"] for r in receiver.perftest_results)
    metrics = {
        "agg_sender_gbps": sender_total,
        "agg_receiver_gbps": receiver_total,
    }
    return 0, metrics, receiver, senders, failed


def main():
    args = parse_args()
    bf_set = {h.strip() for h in args.bluefield_hosts.split(",") if h.strip()}
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    runs_root = Path.cwd() / "runs" / timestamp
    runs_root.mkdir(parents=True, exist_ok=True)

    if args.runs == 1:
        rc, _, receiver, senders, failed = run_once(args, bf_set, runs_root, datetime.now())
        if rc == 0:
            print_terminal_summary(receiver, senders, runs_root, failed)
        sys.exit(rc)

    # Multi-run
    per_run_metrics = []
    run_dirs = []
    for n in range(1, args.runs + 1):
        sub = runs_root / f"run-{n:02d}"
        sub.mkdir(parents=True, exist_ok=True)
        run_dirs.append(sub)
        print(bold(f"\n=== Run {n}/{args.runs} ==="))
        rc, metrics, receiver, senders, failed = run_once(args, bf_set, sub, datetime.now())
        if rc != 0:
            print(red(f"\nRun {n} aborted; bailing."))
            sys.exit(rc)
        per_run_metrics.append(metrics)
        print_terminal_summary(receiver, senders, sub, failed)
    write_aggregate(runs_root / "aggregate.md", run_dirs, per_run_metrics)
    print(f"\n{bold('Aggregate report:')} {runs_root / 'aggregate.md'}")
    sys.exit(0)


if __name__ == "__main__":
    main()