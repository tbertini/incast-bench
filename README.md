# incast-bench

A Mac-side orchestrator for repeatable RDMA incast benchmarks across the ETHZ
Systems Group cluster. Brings every host to a known baseline, snapshots NIC
counters, runs `ib_write_bw` in an N:1 incast pattern, and produces a
markdown report with throughput and counter deltas.

## One-time setup

```bash
# 1. Place the script
mkdir -p ~/Desktop/incast-bench
mv incast-bench ~/Desktop/incast-bench/
chmod +x ~/Desktop/incast-bench/incast-bench

# 2. Set up SSH keys (one prompt per host; only needed once)
ssh-keygen -t ed25519        # skip if you already have ~/.ssh/id_ed25519
for H in alveo-u50d-01 alveo-u50d-02 alveo-u55c-05 alveo-u55c-06; do
    ssh-copy-id tbertini@$H
done

# 3. Optional: add to PATH
ln -s ~/Desktop/incast-bench/incast-bench ~/bin/incast-bench
```

After step 2, all subsequent SSH connections (including the script) need no
password.

## Usage

```bash
# Smallest case: 1:1, 10s
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 -D 10

# Original 3:1 incast from your Apr 1 experiment
incast-bench -r alveo-u50d-02 \
             -s alveo-u50d-01 alveo-u55c-05 alveo-u55c-06

# Reproducibility check — 5 back-to-back runs with mean ± stddev
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 alveo-u55c-05 --runs 5

# Tweak parameters
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 \
             -D 60 -S 8192 --base-port 20000

# Skip baseline if nothing changed (faster iteration)
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 --skip-baseline

# Tag a run with a reason (appears in report + directory name)
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 \
             -m "Baseline before Geert enables PFC on switch"

# Force interleaved mode (no iTerm2 windows)
incast-bench -r alveo-u50d-02 -s alveo-u50d-01 --mode interleaved

incast-bench --help
```

## What it does — 7 phases per run

1. **Connect** — Opens one SSH ControlMaster connection per host. Subsequent
   commands reuse those masters (no reauthentication, very fast).
2. **Detect** — Probes each host for `/opt/mellanox/doca` (BlueField
   marker) and for the `rigi-bluefield` script.
3. **Visibility** — On macOS+iTerm2, opens one window per host that tails the
   corresponding **local** log file. Otherwise streams interleaved
   `[host] line` output to the orchestrator's terminal. Nothing is written on
   the remote machines.
4. **Baseline** — Runs the full setup sequence on every host: `datanic down
   data2` (only on `alveo-u50d-02` for the known bug), MTU=4200, trust-dscp,
   PFC prio 3, ECN-NP/RP prio 3, TC mapping, buffer (TC3=239040), and PCC
   counter init on BlueField hosts. Skipped per-host if `rigi-bluefield`
   isn't available there.
5. **Snapshot pre** — Captures `ethtool -S data1`,
   `/sys/class/infiniband/mlx5_0/ports/1/{counters,hw_counters}/*`, and
   `rigi-bluefield pcc-query` (BF only) into a flat dict.
6. **Run** — Spawns N `ib_write_bw` server instances on the receiver
   (one per port, starting at `--base-port`), waits 2s for them to bind,
   then launches one `ib_write_bw` client per sender, each targeting a
   distinct port. Output from each is captured and parsed.
7. **Snapshot post** — Same as step 5; differences become the report.

## Output

Each run creates a directory under `~/Desktop/incast-bench/runs/`. If you
pass `-m`, the reason is slugified into the directory name so you can tell
runs apart at a glance:

```
runs/
├── 2026-04-15T14-23-09/
├── 2026-04-16T09-30-12--baseline-before-pfc/
│   ├── report.md              ← rendered report (also printed to stdout)
│   ├── alveo-u50d-02.log      ← raw log for receiver
│   ├── alveo-u50d-01.log      ← raw log for sender 1
│   ├── alveo-u55c-05.log
│   └── alveo-u55c-06.log
├── 2026-04-16T10-15-44--pfc-enabled-by-geert/
└── 2026-04-16T10-20-00--pfc-enabled-3-senders/
```

The reason also appears as a blockquote in the report header.

The per-host logs contain every command that was run, every byte of
stdout/stderr returned, and timestamped section headers — so if anything
looks fishy in the report, the raw evidence is right there.

## Headline counters in the report

Always shown (even if zero), so you can confirm pipelines are working:

- **`hw_counters/*`:** `np_cnp_sent`, `rp_cnp_handled`,
  `np_ecn_marked_roce_packets`, `out_of_buffer`, `out_of_sequence`,
  `packet_seq_err`, `local_ack_timeout_err`, `implied_nak_seq_err`,
  `rnr_nak_retry_err`
- **ethtool:** `rx_ecn_mark`, `rx_discards_phy`, `rx_prio[0,3]_pause`,
  `tx_prio[0,3]_pause`
- **IB port `counters/*`:** `port_rcv_errors`, `port_xmit_discards`,
  `port_xmit_data`, `port_rcv_data`

A separate "Other non-zero deltas" section captures everything else that
changed during the run (collapsible per host in the rendered markdown).

## Troubleshooting

- **"Cannot SSH to ..."** — You haven't set up SSH keys yet. Re-run the
  `ssh-keygen` + `ssh-copy-id` sequence above.
- **"rigi-bluefield not installed on this host"** — the U55c machines (or
  any new host) don't have Geert's script. Either ask Geert to deploy it
  there, or accept that baseline configuration is skipped on that host.
- **"address already in use"** — A previous `ib_write_bw` server is still
  bound. SSH in and `pkill -f ib_write_bw`.
- **iTerm2 windows don't open** — Make sure iTerm2 has Accessibility
  permissions (System Settings → Privacy & Security → Accessibility) so
  AppleScript can drive it.
- **Inconsistent throughput** — Pass `--skip-baseline` and run twice in
  quick succession to confirm baseline drift isn't the issue. If still
  inconsistent, the switch state is the variable (which is the whole
  point of these reproducibility runs with Geert/Derk).

## Requirements

- Python 3.9+ (stdlib only — no `pip install`)
- macOS with iTerm2 *recommended* but not required
- Passwordless SSH to all hosts
- `rigi-bluefield` and `perftest` installed on the remote hosts