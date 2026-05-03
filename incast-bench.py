#!/usr/bin/env python3
"""
incast-bench — RDMA incast benchmark orchestrator (ETHZ Systems Group).

Coordinates ib_write_bw runs across multiple senders → one receiver, via SSH
ControlMaster (one connection per host). Captures per-host output into local
log files; optionally opens iTerm2 windows tailing those logs for live
visibility. Snapshots NIC counters before/after and produces a markdown
report with deltas.

Requirements:
  * Python 3.9+, stdlib only
  * Passwordless SSH to all hosts (set up once via `ssh-copy-id`)
  * `rigi-bluefield` and `perftest` installed on the remote hosts
  * macOS with iTerm2 (optional — falls back to interleaved mode otherwise)

Usage:
  incast-bench -r alveo-u50d-02 -s alveo-u50d-01 alveo-u55c-05 alveo-u55c-06
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# -----------------------------------------------------------------------------
# Constants & defaults
# -----------------------------------------------------------------------------

DEFAULT_USER = "tbertini"
DEFAULT_DOMAIN = ""
DEFAULT_DURATION = 30
DEFAULT_MSG_SIZE = 65536
DEFAULT_GID_INDEX = 3
DEFAULT_BASE_PORT = 18515
DEFAULT_DEVICE = "mlx5_0"
DEFAULT_IFACE = "data1"
DATA_SUFFIX = "-data1"  # senders connect to <receiver>-data1

INCAST_ROOT = Path.home() / "Desktop" / "incast-bench"
RUNS_DIR = INCAST_ROOT / "runs"
SSH_CTRL_DIR = INCAST_ROOT / ".ssh-ctrl"

BF_MST_PATH = "/dev/mst/mt41692_pciconf0"
IB_COUNTERS_DIR = "/sys/class/infiniband/mlx5_0/ports/1/counters"
IB_HW_COUNTERS_DIR = "/sys/class/infiniband/mlx5_0/ports/1/hw_counters"

# Counters always shown in the headline section of the report (even when 0)
HEADLINE_HW = [
    "np_cnp_sent",
    "rp_cnp_handled",
    "np_ecn_marked_roce_packets",
    "out_of_buffer",
    "out_of_sequence",
    "packet_seq_err",
    "local_ack_timeout_err",
    "implied_nak_seq_err",
    "rnr_nak_retry_err",
]
HEADLINE_ETHTOOL = [
    "rx_ecn_mark",
    "rx_discards_phy",
    "rx_prio0_pause", "rx_prio3_pause",
    "tx_prio0_pause", "tx_prio3_pause",
]
HEADLINE_IB = [
    "port_rcv_errors", "port_xmit_discards",
    "port_xmit_data", "port_rcv_data",
]

ANSI_COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m", "\033[31m"]
ANSI_RESET = "\033[0m"


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------

@dataclass
class Host:
    name: str            # "alveo-u50d-01"
    role: str            # "receiver" or "sender"
    user: str
    domain: str
    log_path: Path
    ctrl_path: Path
    is_bluefield: bool = False
    has_rigi: bool = False
    color: str = ""

    @property
    def fqdn(self) -> str:
        return f"{self.name}.{self.domain}" if self.domain else self.name

    @property
    def short(self) -> str:
        return self.name.replace("alveo-", "")

    def ssh_base(self) -> list[str]:
        return [
            "ssh",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self.ctrl_path}",
            "-o", "ControlPersist=300s",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            f"{self.user}@{self.fqdn}",
        ]


@dataclass
class CmdResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


@dataclass
class PerftestResult:
    host: str
    role: str
    port: int
    bw_avg_gbps: Optional[float] = None
    bw_peak_gbps: Optional[float] = None
    msg_rate_mpps: Optional[float] = None
    raw_output: str = ""
    error: Optional[str] = None


# -----------------------------------------------------------------------------
# Logging helpers
# -----------------------------------------------------------------------------

_log_lock = threading.Lock()


def host_log(host: Host, text: str) -> None:
    """Append text to the host's log file (thread-safe, line-buffered)."""
    if not text:
        return
    if not text.endswith("\n"):
        text += "\n"
    host.log_path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock:
        with host.log_path.open("a") as f:
            f.write(text)
            f.flush()


# -----------------------------------------------------------------------------
# SSH helpers
# -----------------------------------------------------------------------------

def ssh_run(host: Host, cmd: str, *, timeout: int = 120, tee_log: bool = True,
            log_header: Optional[str] = None, force_tty: bool = False) -> CmdResult:
    """Run `cmd` on `host` via the existing ControlMaster connection."""
    if log_header and tee_log:
        host_log(host, f"\n$ {log_header}")
    full = host.ssh_base() + (["-tt"] if force_tty else []) + [cmd]
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        msg = f"[TIMEOUT after {timeout}s] {cmd}"
        if tee_log:
            host_log(host, msg)
        return CmdResult(rc=124, stdout=e.stdout or "", stderr=msg)
    # With -tt, stderr merges into stdout; clean up PTY artifacts
    stdout = p.stdout
    stderr = p.stderr
    if force_tty:
        stdout = stdout.replace("\r\n", "\n").replace("\r", "")
    if tee_log:
        if stdout:
            host_log(host, stdout)
        if stderr:
            host_log(host, "[stderr] " + stderr.replace("\n", "\n[stderr] "))
    return CmdResult(rc=p.returncode, stdout=stdout, stderr=stderr)


def open_master(host: Host) -> None:
    """Establish the ControlMaster connection. Errors out clearly on auth failure."""
    host.ctrl_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = host.ssh_base() + ["true"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError(_ssh_help_message(host, "connection timed out"))
    if p.returncode != 0:
        raise RuntimeError(_ssh_help_message(host, p.stderr.strip() or "unknown failure"))


def close_master(host: Host) -> None:
    cmd = ["ssh", "-o", f"ControlPath={host.ctrl_path}", "-O", "exit",
           f"{host.user}@{host.fqdn}"]
    subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def _ssh_help_message(host: Host, detail: str) -> str:
    return (
        f"\n❌ Cannot SSH to {host.fqdn}.\n"
        f"   Reason: {detail}\n\n"
        f"   This script REQUIRES passwordless SSH (key-based auth).\n"
        f"   Set it up once with:\n\n"
        f"     ssh-keygen -t ed25519           # only if you don't have a key\n"
        f"     ssh-copy-id {host.user}@{host.fqdn}\n\n"
        f"   Then re-run this script.\n"
    )


# -----------------------------------------------------------------------------
# Detection
# -----------------------------------------------------------------------------

def detect_capabilities(host: Host) -> tuple[bool, bool]:
    """Return (is_bluefield, has_rigi_bluefield)."""
    cmd = (
        "if [ -d /opt/mellanox/doca ]; then echo BF=yes; else echo BF=no; fi; "
        "if command -v rigi-bluefield >/dev/null 2>&1; then echo RIGI=yes; else echo RIGI=no; fi"
    )
    r = ssh_run(host, cmd, tee_log=True, log_header="detect capabilities", timeout=15)
    is_bf = "BF=yes" in r.stdout
    has_rigi = "RIGI=yes" in r.stdout
    return is_bf, has_rigi


# -----------------------------------------------------------------------------
# Baseline configuration
# -----------------------------------------------------------------------------

def apply_baseline(host: Host) -> bool:
    """Apply baseline config to a host. Tolerant of individual step failures."""
    steps: list[tuple[str, str, bool]] = []  # (label, cmd, requires_rigi)

    if host.name == "alveo-u50d-02":
        # Workaround for known data2 bug on this machine specifically
        steps.append(("datanic-down-data2", "sudo datanic down data2 || true", False))

    steps += [
        ("mtu-4200",   f"sudo rigi-bluefield sysfs write /sys/class/net/{DEFAULT_IFACE}/mtu 4200", True),
        ("trust-dscp", "sudo rigi-bluefield set trust-dscp",                                       True),
        ("pfc-prio3",  "sudo rigi-bluefield set pfc-prio3",                                        True),
        ("ecn-np-3",   "sudo rigi-bluefield set ecn-np 3",                                         True),
        ("ecn-rp-3",   "sudo rigi-bluefield set ecn-rp 3",                                         True),
        ("tc-mapping", "sudo rigi-bluefield set tc-mapping",                                       True),
        ("buffer",     "sudo rigi-bluefield set buffer",                                           True),
    ]
    if host.is_bluefield:
        steps.append(("pcc-init", "sudo rigi-bluefield set pcc", True))

    all_ok = True
    skipped_rigi = False
    for label, cmd, needs_rigi in steps:
        if needs_rigi and not host.has_rigi:
            skipped_rigi = True
            continue
        r = ssh_run(host, cmd, tee_log=True, log_header=f"baseline:{label}",
                    timeout=30, force_tty=True)
        if not r.ok:
            host_log(host, f"  ⚠️  baseline step '{label}' failed (rc={r.rc})")
            all_ok = False
    if skipped_rigi:
        host_log(host, "  ⚠️  rigi-bluefield not installed on this host — baseline steps that "
                       "depend on it were skipped.")
    return all_ok


# -----------------------------------------------------------------------------
# Counter snapshots
# -----------------------------------------------------------------------------

SNAPSHOT_SCRIPT = f"""
echo '===ETHTOOL==='
ethtool -S {DEFAULT_IFACE} 2>/dev/null
echo '===IB_COUNTERS==='
for f in {IB_COUNTERS_DIR}/*; do
  [ -f "$f" ] && echo "$(basename $f): $(cat $f 2>/dev/null)"
done
echo '===IB_HW_COUNTERS==='
for f in {IB_HW_COUNTERS_DIR}/*; do
  [ -f "$f" ] && echo "$(basename $f): $(cat $f 2>/dev/null)"
done
"""


def snapshot_counters(host: Host, label: str) -> dict[str, int]:
    # Non-sudo counters (ethtool + IB)
    r = ssh_run(host, SNAPSHOT_SCRIPT, tee_log=True, log_header=f"snapshot:{label}", timeout=30)
    if not r.ok:
        return {}
    counters = parse_counters(r.stdout)
    # PCC counters require sudo → force TTY
    if host.is_bluefield and host.has_rigi:
        pcc_cmd = "echo '===PCC_QUERY==='; sudo rigi-bluefield pcc-query 2>/dev/null"
        r2 = ssh_run(host, pcc_cmd, tee_log=True, log_header=f"snapshot:{label}:pcc",
                     timeout=30, force_tty=True)
        if r2.ok:
            counters.update(parse_counters(r2.stdout))
    return counters


_PCC_LINE_RE = re.compile(r"Counter:\s*(\S+)\s+Value:\s*([0-9a-fA-F]+)")


def parse_counters(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("===") and line.endswith("==="):
            section = line.strip("= ")
            continue
        if not line:
            continue
        if section == "PCC_QUERY":
            m = _PCC_LINE_RE.search(line)
            if m:
                # pcc-query output is zero-padded 16-digit hex
                try:
                    out["pcc__" + m.group(1)] = int(m.group(2), 16)
                except ValueError:
                    pass
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key or not val:
            continue
        try:
            n = int(val)
        except ValueError:
            continue
        prefix = {
            "ETHTOOL": "ethtool__",
            "IB_COUNTERS": "ib__",
            "IB_HW_COUNTERS": "hw__",
        }.get(section, "")
        if prefix:
            out[prefix + key] = n
    return out


def diff_counters(pre: dict[str, int], post: dict[str, int]) -> dict[str, int]:
    deltas: dict[str, int] = {}
    keys = set(pre) | set(post)
    for k in keys:
        d = post.get(k, 0) - pre.get(k, 0)
        if d != 0:
            deltas[k] = d
    return deltas


# -----------------------------------------------------------------------------
# Benchmark execution
# -----------------------------------------------------------------------------

# Standard ib_write_bw output line:
#  #bytes  #iterations  BW peak[Gb/sec]  BW average[Gb/sec]  MsgRate[Mpps]
#  65536   150000        92.50              92.39                 0.18
_PERFTEST_LINE_RE = re.compile(
    r"^\s*\d+\s+\d+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$"
)


def parse_perftest(text: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (bw_peak, bw_avg, msg_rate)."""
    for line in text.splitlines():
        m = _PERFTEST_LINE_RE.match(line)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None, None, None


def _run_receiver_port(host: Host, port: int, args) -> PerftestResult:
    """Run a single ib_write_bw server instance on one port."""
    bw_cmd = (f"ib_write_bw -d {DEFAULT_DEVICE} -x {args.gid_index} -F --report_gbits "
              f"-D {args.duration} -s {args.msg_size} -p {port}")
    r = ssh_run(host, bw_cmd, tee_log=True,
                log_header=f"receiver port {port}",
                timeout=args.duration + 60)
    peak, avg, mpps = parse_perftest(r.stdout)
    return PerftestResult(
        host=host.name, role="receiver", port=port,
        bw_avg_gbps=avg, bw_peak_gbps=peak, msg_rate_mpps=mpps,
        raw_output=r.stdout,
        error=None if r.ok else (r.stderr.strip() or f"rc={r.rc}"),
    )


def run_receiver(host: Host, ports: list[int], args) -> dict[int, PerftestResult]:
    """Spawn N ib_write_bw servers in parallel (one SSH call per port)."""
    with ThreadPoolExecutor(max_workers=len(ports)) as ex:
        futures = {port: ex.submit(_run_receiver_port, host, port, args) for port in ports}
        return {port: fut.result() for port, fut in futures.items()}


def run_sender(host: Host, port: int, receiver_addr: str, args,
               duration_override: Optional[int] = None) -> PerftestResult:
    duration = duration_override if duration_override is not None else args.duration
    bw_cmd = (f"ib_write_bw -d {DEFAULT_DEVICE} -x {args.gid_index} -F --report_gbits "
              f"-D {duration} -s {args.msg_size} -p {port} {receiver_addr}")
    r = ssh_run(host, bw_cmd, tee_log=True,
                log_header=f"sender → {receiver_addr}:{port} (-D {duration})",
                timeout=args.duration + 60)
    peak, avg, mpps = parse_perftest(r.stdout)
    return PerftestResult(
        host=host.name, role="sender", port=port,
        bw_avg_gbps=avg, bw_peak_gbps=peak, msg_rate_mpps=mpps,
        raw_output=r.stdout,
        error=None if r.ok else (r.stderr.strip() or f"rc={r.rc}"),
    )


# -----------------------------------------------------------------------------
# Visibility: iTerm2 windows OR interleaved fallback
# -----------------------------------------------------------------------------

def open_iterm2_windows(hosts: list[Host]) -> bool:
    """One iTerm2 window per host, each tailing its local log file."""
    if not shutil.which("osascript"):
        return False
    if not Path("/Applications/iTerm.app").exists():
        return False
    for h in hosts:
        h.log_path.parent.mkdir(parents=True, exist_ok=True)
        h.log_path.touch()
        title = f"{h.role}: {h.short}"
        inner = (f"clear; printf '\\033]0;{title}\\007'; "
                 f"echo '── tailing {h.log_path} ──'; "
                 f"tail -n +1 -F {shlex.quote(str(h.log_path))}")
        # The string passed to AppleScript needs double-quotes escaped.
        inner_as = inner.replace("\\", "\\\\").replace('"', '\\"')
        title_as = title.replace('"', '\\"')
        script = (
            'tell application "iTerm"\n'
            '    activate\n'
            '    create window with default profile\n'
            '    tell current session of current window\n'
            f'        write text "{inner_as}"\n'
            f'        set name to "{title_as}"\n'
            '    end tell\n'
            'end tell\n'
        )
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=10)
        if p.returncode != 0:
            sys.stderr.write(f"  ⚠️  Failed to open iTerm2 window for {h.name}: "
                             f"{p.stderr.strip()}\n")
            return False
    return True


class InterleavedTailer:
    """Stream each host's log file to stdout with a colored prefix."""

    def __init__(self, hosts: list[Host]):
        self.hosts = hosts
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.print_lock = threading.Lock()

    def start(self):
        for h in self.hosts:
            h.log_path.parent.mkdir(parents=True, exist_ok=True)
            h.log_path.touch()
            t = threading.Thread(target=self._tail, args=(h,), daemon=True)
            t.start()
            self.threads.append(t)

    def stop_all(self):
        self.stop.set()
        time.sleep(0.3)  # let final lines flush

    def _tail(self, host: Host):
        prefix = f"{host.color}[{host.short}]{ANSI_RESET} "
        with host.log_path.open("r") as f:
            f.seek(0, os.SEEK_END)
            while not self.stop.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                with self.print_lock:
                    sys.stdout.write(prefix + line)
                    sys.stdout.flush()


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------

def fmt_bytes(n: int) -> str:
    """Pretty-print byte counts (port_xmit_data is in 4-byte units, but show raw)."""
    if abs(n) >= 1_000_000_000:
        return f"{n/1e9:.2f}G"
    if abs(n) >= 1_000_000:
        return f"{n/1e6:.2f}M"
    if abs(n) >= 1_000:
        return f"{n/1e3:.2f}K"
    return str(n)


def build_report(*, args, hosts: list[Host], receiver: Host,
                 receiver_results: dict[int, PerftestResult],
                 sender_results: dict[str, PerftestResult],
                 counter_diffs: dict[str, dict[str, int]],
                 wall_time_s: float, run_dir: Path) -> str:
    lines: list[str] = []
    lines.append(f"# Incast benchmark report — {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    if args.reason:
        lines.append(f"> **Reason:** {args.reason}")
        lines.append("")
    lines.append("## Configuration")
    lines.append(f"- Receiver: **{receiver.name}** ({len(receiver_results)} server instances)")
    lines.append(f"- Senders: " + ", ".join(f"**{s}**" for s in sender_results))
    lines.append(f"- ib_write_bw: `-D {args.duration} -s {args.msg_size} "
                 f"-x {args.gid_index} --report_gbits`")
    if args.stagger_stop > 0:
        keep = args.keep_running or list(sender_results)[0]
        lines.append(f"- Stagger-stop: `{keep}` runs for {args.duration}s; "
                     f"other senders run for {args.duration - args.stagger_stop}s "
                     f"(stop {args.stagger_stop}s early)")
    lines.append(f"- Base port: {args.base_port}  →  ports "
                 f"{args.base_port}..{args.base_port + len(sender_results) - 1}")
    lines.append(f"- Wall-clock: {wall_time_s:.1f}s")
    lines.append(f"- Logs: `{run_dir}`")
    lines.append("")

    # Throughput table
    lines.append("## Throughput")
    lines.append("")
    lines.append("| Host | Role | Port | BW avg (Gb/s) | BW peak (Gb/s) | Msg rate (Mpps) |")
    lines.append("|------|------|-----:|--------------:|---------------:|----------------:|")
    total_sender = 0.0
    total_receiver = 0.0
    for s_name, sr in sender_results.items():
        avg = f"{sr.bw_avg_gbps:.2f}" if sr.bw_avg_gbps is not None else "—"
        peak = f"{sr.bw_peak_gbps:.2f}" if sr.bw_peak_gbps is not None else "—"
        mpps = f"{sr.msg_rate_mpps:.2f}" if sr.msg_rate_mpps is not None else "—"
        if sr.bw_avg_gbps:
            total_sender += sr.bw_avg_gbps
        err = f"  ⚠️ {sr.error}" if sr.error else ""
        lines.append(f"| {s_name} | sender | {sr.port} | {avg} | {peak} | {mpps} |{err}")
    for p, rr in sorted(receiver_results.items()):
        avg = f"{rr.bw_avg_gbps:.2f}" if rr.bw_avg_gbps is not None else "—"
        peak = f"{rr.bw_peak_gbps:.2f}" if rr.bw_peak_gbps is not None else "—"
        mpps = f"{rr.msg_rate_mpps:.2f}" if rr.msg_rate_mpps is not None else "—"
        if rr.bw_avg_gbps:
            total_receiver += rr.bw_avg_gbps
        lines.append(f"| {receiver.name} | receiver | {p} | {avg} | {peak} | {mpps} |")
    lines.append("")
    lines.append(f"**Aggregate sender BW: {total_sender:.2f} Gb/s · "
                 f"Aggregate receiver BW: {total_receiver:.2f} Gb/s**")
    lines.append("")

    # Headline counters table
    lines.append("## Headline counter deltas (Δ pre→post)")
    lines.append("")
    headline_pairs = (
        [("hw__" + n, n) for n in HEADLINE_HW] +
        [("ethtool__" + n, n) for n in HEADLINE_ETHTOOL] +
        [("ib__" + n, n) for n in HEADLINE_IB]
    )
    cols = "| Counter | " + " | ".join(h.short for h in hosts) + " |"
    sep = "|---|" + "|".join("---:" for _ in hosts) + "|"
    lines.append(cols)
    lines.append(sep)
    for key, label in headline_pairs:
        cells = []
        for h in hosts:
            v = counter_diffs.get(h.name, {}).get(key, 0)
            if v == 0:
                cells.append(".")
            elif "data" in label:
                cells.append(fmt_bytes(v))
            else:
                cells.append(f"{v:,}")
        lines.append(f"| `{label}` | " + " | ".join(cells) + " |")
    lines.append("")

    # PCC counters (BlueField only)
    bf_hosts = [h for h in hosts if h.is_bluefield]
    if bf_hosts:
        pcc_keys = sorted({k for h in bf_hosts
                           for k in counter_diffs.get(h.name, {})
                           if k.startswith("pcc__")})
        nonzero_pcc = [k for k in pcc_keys
                       if any(counter_diffs.get(h.name, {}).get(k, 0) != 0
                              for h in bf_hosts)]
        if nonzero_pcc:
            lines.append("## PCC counter deltas (BlueField hosts)")
            lines.append("")
            cols = "| Counter | " + " | ".join(h.short for h in bf_hosts) + " |"
            sep = "|---|" + "|".join("---:" for _ in bf_hosts) + "|"
            lines.append(cols)
            lines.append(sep)
            for k in nonzero_pcc:
                cells = []
                for h in bf_hosts:
                    v = counter_diffs.get(h.name, {}).get(k, 0)
                    cells.append(f"{v:,}" if v else ".")
                lines.append(f"| `{k.removeprefix('pcc__')}` | " + " | ".join(cells) + " |")
            lines.append("")

    # Other non-zero deltas (collapsed per host)
    other_sections = []
    headline_keys = {k for k, _ in headline_pairs}
    for h in hosts:
        deltas = counter_diffs.get(h.name, {})
        extras = sorted((k, v) for k, v in deltas.items()
                        if k not in headline_keys and not k.startswith("pcc__"))
        if extras:
            other_sections.append((h, extras))
    if other_sections:
        lines.append("## Other non-zero deltas (per host)")
        lines.append("")
        for h, extras in other_sections:
            lines.append(f"<details><summary><b>{h.name}</b> "
                         f"({len(extras)} counters changed)</summary>")
            lines.append("")
            lines.append("| Counter | Δ |")
            lines.append("|---|---:|")
            for k, v in extras:
                lines.append(f"| `{k}` | {v:,} |")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def parse_args():
    ap = argparse.ArgumentParser(
        description="RDMA incast benchmark orchestrator (ETHZ Systems Group)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("-r", "--receiver", required=True,
                    help="Receiver hostname (e.g. alveo-u50d-02)")
    ap.add_argument("-s", "--senders", required=True, nargs="+",
                    help="Sender hostnames (e.g. alveo-u50d-01 alveo-u55c-05)")
    ap.add_argument("-D", "--duration", type=int, default=DEFAULT_DURATION,
                    help="ib_write_bw duration in seconds (the longest-running sender)")
    ap.add_argument("--stagger-stop", type=int, default=0, metavar="SECONDS",
                    help="Other senders stop SECONDS earlier than --keep-running (0 = all "
                         "stop together). Useful for testing whether a CC algorithm recovers "
                         "to line rate after competing flows quit.")
    ap.add_argument("--keep-running", default="",
                    help="Hostname of the sender that runs for the full --duration. Other "
                         "senders run for (duration - stagger-stop) seconds. Default: first "
                         "host in --senders.")
    ap.add_argument("-S", "--msg-size", type=int, default=DEFAULT_MSG_SIZE,
                    help="ib_write_bw message size in bytes")
    ap.add_argument("-x", "--gid-index", type=int, default=DEFAULT_GID_INDEX,
                    help="RoCEv2 GID index")
    ap.add_argument("--base-port", type=int, default=DEFAULT_BASE_PORT,
                    help="First TCP port for ib_write_bw; ports increment per sender")
    ap.add_argument("-m", "--reason", default="",
                    help="Why you're running this benchmark (appears in report + directory name)")
    ap.add_argument("--runs", type=int, default=1,
                    help="Number of repeated identical runs (mean ± stddev summary)")
    ap.add_argument("--user", default=DEFAULT_USER, help="SSH user")
    ap.add_argument("--domain", default=DEFAULT_DOMAIN,
                    help="DNS domain to append to hostnames (empty = rely on DNS search domain)")
    ap.add_argument("--skip-baseline", action="store_true",
                    help="Skip the baseline configuration step")
    ap.add_argument("--mode", choices=["auto", "iterm2", "interleaved"], default="auto",
                    help="Visibility mode (auto picks iTerm2 on macOS if available)")
    return ap.parse_args()


def make_host(name: str, role: str, run_dir: Path,
              user: str, domain: str, color: str) -> Host:
    return Host(
        name=name, role=role, user=user, domain=domain,
        log_path=run_dir / f"{name}.log",
        ctrl_path=SSH_CTRL_DIR / f"cm-{name}.sock",
        color=color,
    )


def run_one_iteration(args, run_dir: Path, run_idx: int, total_runs: int):
    SSH_CTRL_DIR.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Build host list (receiver gets a distinct color)
    receiver = make_host(args.receiver, "receiver", run_dir,
                         args.user, args.domain, ANSI_COLORS[0])
    senders = [
        make_host(name, "sender", run_dir, args.user, args.domain,
                  ANSI_COLORS[(i + 1) % len(ANSI_COLORS)])
        for i, name in enumerate(args.senders)
    ]
    all_hosts = [receiver] + senders

    print(f"\n{'='*72}")
    print(f"  Run {run_idx+1}/{total_runs}  →  {run_dir}")
    if args.reason:
        print(f"  Reason: {args.reason}")
    print(f"{'='*72}\n")

    # Phase 1 — open ControlMaster connections
    print("[1/7] Opening SSH ControlMaster connections...")
    with ThreadPoolExecutor(max_workers=len(all_hosts)) as ex:
        futs = {h.name: ex.submit(open_master, h) for h in all_hosts}
        for name, fut in futs.items():
            try:
                fut.result()
                print(f"      ✓ {name}")
            except RuntimeError as e:
                print(str(e), file=sys.stderr)
                raise

    # Phase 2 — detect capabilities
    print("\n[2/7] Detecting BlueField vs ConnectX, checking rigi-bluefield...")
    with ThreadPoolExecutor(max_workers=len(all_hosts)) as ex:
        for h, (is_bf, has_rigi) in zip(all_hosts, ex.map(detect_capabilities, all_hosts)):
            h.is_bluefield = is_bf
            h.has_rigi = has_rigi
            kind = "BlueField" if is_bf else "ConnectX"
            rigi = "✓ rigi" if has_rigi else "✗ no rigi-bluefield (baseline will be skipped)"
            print(f"      {h.name}: {kind}, {rigi}")

    # Phase 3 — visibility windows
    print("\n[3/7] Opening visibility windows...")
    mode = args.mode
    if mode == "auto":
        mode = "iterm2" if sys.platform == "darwin" else "interleaved"
    tailer = None
    if mode == "iterm2":
        if open_iterm2_windows(all_hosts):
            print("      ✓ iTerm2 windows opened (one per host).")
        else:
            print("      iTerm2 unavailable — falling back to interleaved mode.")
            mode = "interleaved"
    if mode == "interleaved":
        tailer = InterleavedTailer(all_hosts)
        tailer.start()
        print("      ✓ Streaming logs to this terminal with [host] prefixes.")

    try:
        # Phase 4 — baseline
        if not args.skip_baseline:
            print("\n[4/7] Applying baseline configuration on all hosts...")
            with ThreadPoolExecutor(max_workers=len(all_hosts)) as ex:
                results = list(ex.map(apply_baseline, all_hosts))
            ok_count = sum(1 for r in results if r)
            print(f"      ✓ {ok_count}/{len(all_hosts)} hosts fully configured "
                  f"(see per-host logs for any warnings).")
        else:
            print("\n[4/7] Skipping baseline (--skip-baseline).")

        # Phase 5 — counter snapshot (pre)
        print("\n[5/7] Snapshotting counters (pre)...")
        with ThreadPoolExecutor(max_workers=len(all_hosts)) as ex:
            pre_snaps = dict(zip(
                [h.name for h in all_hosts],
                ex.map(lambda h: snapshot_counters(h, "pre"), all_hosts),
            ))
        for name, snap in pre_snaps.items():
            print(f"      ✓ {name}: {len(snap)} counters captured")

        # Phase 6 — run benchmark
        ports = [args.base_port + i for i in range(len(senders))]
        sender_to_port = dict(zip([s.name for s in senders], ports))
        receiver_addr = receiver.name + DATA_SUFFIX

        # Stagger-stop: --keep-running runs for full duration, others run shorter.
        keep_running = args.keep_running or senders[0].name
        if keep_running not in {s.name for s in senders}:
            raise RuntimeError(
                f"--keep-running '{keep_running}' is not in --senders. "
                f"Senders: {[s.name for s in senders]}"
            )
        short_duration = args.duration - args.stagger_stop
        if args.stagger_stop > 0 and short_duration < 1:
            raise RuntimeError(
                f"--stagger-stop {args.stagger_stop}s is >= --duration {args.duration}s; "
                f"other senders would never run."
            )
        sender_durations = {
            s.name: args.duration if s.name == keep_running else short_duration
            for s in senders
        }

        if args.stagger_stop > 0:
            others = [n for n, d in sender_durations.items() if d != args.duration]
            print(f"\n[6/7] Running benchmark: {len(senders)} senders → "
                  f"{receiver.name} ({receiver_addr}), ports {ports[0]}..{ports[-1]}")
            print(f"      ↳ {keep_running} runs for {args.duration}s")
            print(f"      ↳ {others} run for {short_duration}s "
                  f"(stop {args.stagger_stop}s early)")
        else:
            print(f"\n[6/7] Running benchmark: {len(senders)} senders → "
                  f"{receiver.name} ({receiver_addr}), ports {ports[0]}..{ports[-1]}, "
                  f"{args.duration}s + handshake...")

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=1 + len(senders)) as ex:
            recv_future = ex.submit(run_receiver, receiver, ports, args)
            time.sleep(2)  # let receivers bind
            sender_futures = {
                s.name: ex.submit(run_sender, s, sender_to_port[s.name],
                                  receiver_addr, args,
                                  duration_override=sender_durations[s.name])
                for s in senders
            }
            sender_results = {name: f.result() for name, f in sender_futures.items()}
            receiver_results = recv_future.result()
        wall_s = time.time() - t0
        print(f"      ✓ Done in {wall_s:.1f}s.")

        # Phase 7 — counter snapshot (post)
        print("\n[7/7] Snapshotting counters (post)...")
        with ThreadPoolExecutor(max_workers=len(all_hosts)) as ex:
            post_snaps = dict(zip(
                [h.name for h in all_hosts],
                ex.map(lambda h: snapshot_counters(h, "post"), all_hosts),
            ))
        counter_diffs = {
            name: diff_counters(pre_snaps[name], post_snaps[name])
            for name in pre_snaps
        }

        report = build_report(
            args=args, hosts=all_hosts, receiver=receiver,
            receiver_results=receiver_results, sender_results=sender_results,
            counter_diffs=counter_diffs, wall_time_s=wall_s, run_dir=run_dir,
        )
        report_path = run_dir / "report.md"
        report_path.write_text(report)

    finally:
        if tailer is not None:
            tailer.stop_all()
        for h in all_hosts:
            close_master(h)

    print("\n" + "=" * 72)
    print(report)
    print("=" * 72)
    print(f"\nReport saved to {report_path}")
    print(f"Raw logs in    {run_dir}\n")

    return sender_results, receiver_results, run_dir


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    iteration_summaries = []

    # Build a slug from the reason for the directory name
    reason_slug = ""
    if args.reason:
        slug = re.sub(r'[^a-zA-Z0-9]+', '-', args.reason).strip('-').lower()[:60]
        if slug:
            reason_slug = f"--{slug}"

    for run_idx in range(args.runs):
        run_label = timestamp if args.runs == 1 else f"{timestamp}-run{run_idx+1}"
        run_label += reason_slug
        run_dir = RUNS_DIR / run_label
        try:
            sender_results, receiver_results, _ = run_one_iteration(
                args, run_dir, run_idx, args.runs,
            )
        except RuntimeError as e:
            sys.stderr.write(str(e))
            return 2
        iteration_summaries.append((sender_results, receiver_results))

    # Multi-run summary
    if args.runs > 1:
        print("=" * 72)
        print(f"  Cross-run summary ({args.runs} runs)")
        print("=" * 72)
        per_sender_bws: dict[str, list[float]] = {}
        for srs, _ in iteration_summaries:
            for name, sr in srs.items():
                if sr.bw_avg_gbps is not None:
                    per_sender_bws.setdefault(name, []).append(sr.bw_avg_gbps)
        print()
        print(f"{'Sender':<22} {'mean (Gb/s)':>14} {'stddev':>10} "
              f"{'min':>8} {'max':>8}")
        print("-" * 64)
        for name, bws in per_sender_bws.items():
            mean = sum(bws) / len(bws)
            var = sum((b - mean) ** 2 for b in bws) / len(bws)
            std = var ** 0.5
            print(f"{name:<22} {mean:>14.2f} {std:>10.2f} "
                  f"{min(bws):>8.2f} {max(bws):>8.2f}")
        print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n\nInterrupted.\n")
        sys.exit(130)
