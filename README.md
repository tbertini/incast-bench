# incast-bench

Single-file Python orchestrator for many-sender → one-receiver RoCEv2 incast
experiments on the ETHZ Systems Group `alveo-*` cluster. Drives `ib_write_bw`,
snapshots Mellanox NIC counters before and after, and produces a Markdown
report with throughput, headline counter deltas, and per-host details.

Built specifically for BlueField-3 + ConnectX RoCEv2 work: bakes in the
DCQCN/PFC/ECN baseline on BF3 hosts via `rigi-bluefield`, tracks per-host PCC
availability, and uses the right TOS by default so traffic actually lands on
prio 3 with PFC/ECN active.

## Requirements

- Python 3.9+ (stdlib only — no `pip install` step)
- macOS or Linux (or WSL2 on Windows). The script relies on OpenSSH
  ControlMaster, which native Windows OpenSSH does not support.
- Passwordless SSH to every host in the run, with the agent loaded:
  ```
  ssh-add -l                    # should list a key
  ssh-add ~/.ssh/id_ed25519     # if empty
  ```
- BlueField-3 hosts: `rigi-bluefield`, `mlxreg` permissions, and `mlnx_qos`
  available to the SSH user (or via passwordless `sudo`)
- All hosts: `ib_write_bw` from `perftest`, `ethtool`, RDMA dev `mlx5_0`,
  net IF `data1` (these are hardcoded as constants near the top of the
  script — edit `DEFAULT_RDMA_DEV` / `DEFAULT_NET_IF` if your fleet
  differs)

## Quickstart

The `--user` flag is required unless your local username matches the remote
account on every host (uncommon — most lab accounts differ from your laptop's
`$USER`). Examples below assume `my_username`; substitute your own.

```
$ ./incast-bench.py --user my_username -r alveo-u50d-02 -s alveo-u50d-01 -D 30
```

Output goes to `runs/<timestamp>/` under your **current working directory**
(not the script's directory). With `iTerm2` on macOS, one window per host
opens automatically and tails the per-host log; otherwise the run streams
interleaved colored output to the terminal.

Three senders against one receiver:

```
$ ./incast-bench.py --user my_username -r alveo-u50d-02 \
    -s alveo-u50d-01 alveo-u55c-03 alveo-u55c-04 -D 60
```

Repeat to get mean ± stddev:

```
$ ./incast-bench.py --user my_username -r alveo-u50d-02 \
    -s alveo-u50d-01 alveo-u55c-03 -D 30 --runs 5
```

Tip: if you always use the same SSH user, drop it into a shell alias so you
can stop typing `--user`:

```
alias incast='~/Desktop/incast-bench/incast-bench.py --user my_username'
```

## CLI

| Flag | Default | Description |
|---|---|---|
| `-r, --receiver` | required | Single receiver hostname |
| `-s, --senders` | required | One or more sender hostnames |
| `-D, --duration` | 30 | `ib_write_bw` duration, seconds |
| `-S, --msg-size` | 65536 | Message size, bytes |
| `-x, --gid-index` | 3 | RoCEv2 GID index (`show_gids` to find it) |
| `--base-port` | 18515 | First TCP port; one allocated per sender |
| `--runs` | 1 | Repeat N times for mean ± stddev |
| `--user` | `$USER` | SSH user. Required if local username doesn't match remote. |
| `--skip-baseline` | off | Skip the rigi-bluefield PFC/trust/ECN/PCC baseline (use when hosts are already configured for the run) |
| `--mode` | auto | `auto` / `iterm2` / `interleaved` |
| `--bluefield-hosts` | `alveo-u50d-01,alveo-u50d-02` | BF3 allowlist |
| `--tos` | 105 | TOS byte. **See note below.** |

### About `--tos 105`

Default is **deliberately 105**, not 104. The TOS byte splits as 6 DSCP bits +
2 ECN bits:

```
 7   6   5   4   3   2     1   0
[      DSCP = 26      ]   [ ECN ]
```

- `--tos 104` = DSCP 26, ECN bits `00` (Not-ECT) — switch will **drop** on congestion
- `--tos 105` = DSCP 26, ECN bits `01` (ECT(1)) — switch can **mark** on congestion ✓
- `--tos 106` = DSCP 26, ECN bits `10` (ECT(0)) — also markable
- `--tos 107` = DSCP 26, ECN bits `11` (CE)     — already-marked

If you forget the ECN bits and use plain DSCP-shifted-left-by-two, you get
Not-ECT and ECN never fires. This is an easy trap to fall into — looking at
the DSCP value alone makes 104 seem right.

`-R` is also passed to `ib_write_bw` because `--tos` is a no-op without
rdma_cm — the TOS field only takes effect when the QP is set up via the CM.

## What it does, phase by phase

1. **Open SSH ControlMaster** to every host in parallel. Subsequent
   commands multiplex over the persistent connection. If every host fails
   with "Permission denied", the script suggests `ssh-add -l`.
2. **Pkill stale `ib_write_bw`** on every host. Receivers from a previous
   crashed/Ctrl+C'd run survive their parent SSH session and block port
   18515 — this is the single most common reason a fresh run fails with
   `Couldn't bind`.
3. **Open visibility windows** — one iTerm2 window per host on macOS, or
   interleaved colored output otherwise.
4. **Apply baseline configuration** on BlueField-3 hosts. First, ensure
   Mellanox Software Tools is loaded (`sudo mst start` if
   `/dev/mst/mt*_pciconf0` is missing — typically a one-time-per-boot
   no-op). Then:
   ```
   sudo rigi-bluefield set pfc-prio3
   sudo rigi-bluefield set trust-dscp
   sudo rigi-bluefield set ecn-np --all
   sudo rigi-bluefield set ecn-rp --all
   ```
   Then `pgrep -x doca_pcc` to check whether a PCC algorithm is actually
   running on this host. If yes, also run `sudo rigi-bluefield set pcc` to
   register the diagnostic counters; if no, skip PCC ops entirely (typical
   on the receiver, or on any run without a PCC algorithm loaded).
   Skipped on ConnectX hosts.
5. **Pre-snapshot counters** on every host: `ethtool -S data1`, the
   per-port `counters/` and `hw_counters/` sysfs trees, and (where PCC
   init succeeded) `rigi-bluefield pcc-query`.
6. **Run the benchmark**: spawn one receiver per sender on consecutive
   ports, wait 4s for the receivers to bind, then fire all senders in
   parallel. Tail outputs to per-host logs.
7. **Post-snapshot counters** the same way.

Then write `report.md` and pkill stragglers before tearing down the SSH
masters. On Ctrl+C or any fatal error, the run bails — no partial report —
but pkill cleanup still runs in the `finally` block before ControlMaster
teardown.

## Output layout

```
runs/2026-04-30T15-21-29/
├── alveo-u50d-01.log          # full transcript per host
├── alveo-u50d-02.log
├── alveo-u55c-03.log
└── report.md
```

For `--runs N > 1`:

```
runs/2026-04-30T15-21-29/
├── run-01/
│   ├── <host>.log ...
│   └── report.md
├── run-02/
│   └── ...
└── aggregate.md            # mean / stddev / min / max across runs
```

The report contains:

- **Configuration** — exact CLI invocation
- **Throughput** — per-host BW avg / peak / Mpps, plus aggregate sender and
  aggregate receiver totals
- **Headline counter deltas** — table across all hosts for the counters that
  directly indicate DCQCN signal flow (CNPs, ECN marks, out-of-buffer,
  OOS/seq errors, pause counts, port byte/error counts). Worth eyeballing
  every run.
- **Per-host details** — collapsible `<details>` block per host with all
  other non-zero counter deltas, plus the post-run `pcc-query` output where
  applicable
- **Failed commands** — every command that returned non-zero, with the host
  it ran on and the error message

## Known fleet caveats

These are documented so the symptoms in your report don't surprise you.

- **`rigi-bluefield set tc-mapping` and `set buffer` are skipped.** They
  fail on this fleet because the installed `mlnx_qos` predates the
  `--tc_tsa` flag the rigi script expects. The default prio→TC mapping is
  already correct so they were no-ops anyway.
- **PCC init may fail even when `doca_pcc` is running.** The daemon-presence
  check (`pgrep -x doca_pcc`) gates whether we attempt PCC at all, so on
  hosts without an algorithm loaded you'll just see "configured (no PCC
  daemon)" and the snapshots will skip `pcc-query` cleanly. If the daemon
  IS running but `set pcc` still fails, that means the firmware is in a
  bad state (typically stale registration from a previous PCC session) —
  this is surfaced as a real failure since you expected PCC to work.
  Restart the `doca_pcc` daemon to clear it.
- **Switch ECN-AQM might not be enabled.** If your headline `np_cnp_sent`
  and `rx_ecn_mark` counters stay at zero in incast scenarios that should
  obviously be congested, the switch is probably tail-dropping instead of
  marking. Check with the lab's switch operator.
- **ConnectX hosts may show `tx_prio0_pause` instead of `tx_prio3_pause`.**
  This is cosmetic — `cma_roce_tos` isn't set on those hosts, so `ip` reports
  the egress prio incorrectly. The hardware actually marks at prio 3 (because
  of the DSCP). Note that all `*_pause` counters will read zero unless PFC is
  actively engaging — which on this fleet typically means the switch is
  tail-dropping rather than pausing upstream.

## Troubleshooting

### Every host fails with "Permission denied" at phase 1

```
ssh-add -l                       # is anything loaded?
ssh-add ~/.ssh/id_ed25519        # load the key
```

The script uses `BatchMode=yes` so SSH won't prompt — it'll fail fast
instead. This is intentional: an SSH prompt blocking forever during
parallel host init is worse than failing with a clear error.

### `Couldn't bind` / `EADDRINUSE` on the receiver

A previous run left a receiver running. The orchestrator pkills before
every run, so this should self-heal. If you see it persistently, run
`pkill ib_write_bw` manually on the receiver.

### Throughput is low and all headline counters are zero

Almost certainly a TOS / DSCP-trust mismatch. Check:

1. `--tos` is 105 (or another value with ECN bits set)
2. `rigi-bluefield set trust-dscp` was applied (look for it in the per-host
   log under the `BASELINE` section)
3. The switch supports ECN-AQM and it's enabled

### Receiver `pcc-query` output looks empty / errors

The script auto-detects whether `doca_pcc` is running on each host and
skips PCC ops on hosts without it — so this should only happen if the
daemon is up but in a bad state. Check the per-host log for the `set pcc`
rc, then restart the daemon: `sudo killall doca_pcc` and re-launch your
PCC application. If it persistently fails even with a fresh daemon, that's
a known fleet issue.

### macOS without iTerm2

The script falls back to interleaved colored output in your existing
terminal. If you want the per-host windows, install iTerm2 from
<https://iterm2.com/> or pass `--mode interleaved` explicitly to silence
the auto-detection.