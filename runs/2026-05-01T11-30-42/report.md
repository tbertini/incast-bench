# Incast benchmark report

- **Timestamp:** 2026-05-01T11:30:42
- **Receiver:** `alveo-u50d-02`
- **Senders:** `alveo-u50d-01`, `alveo-u55c-03`, `alveo-u55c-04`
- **Duration:** 60s   **Message size:** 65536 B   **GID idx:** 3   **TOS:** 105 (DSCP 26, ECT(1))

## Configuration

```
$ incast-bench.py --user my_username -r alveo-u50d-02 -s alveo-u50d-01 alveo-u55c-03 alveo-u55c-04 -D 60
```

## Throughput

| Host | Role | Port | BW avg [Gb/s] | BW peak [Gb/s] | MsgRate [Mpps] |
|---|---|---:|---:|---:|---:|
| `alveo-u50d-01` | sender | 18515 | 5.21 | 0.00 | 0.010 |
| `alveo-u55c-03` | sender | 18516 | 4.33 | 0.00 | 0.008 |
| `alveo-u55c-04` | sender | 18517 | 4.32 | 0.00 | 0.008 |
| `alveo-u50d-02` | receiver | 18515 | 5.21 | 0.00 | 0.010 |
| `alveo-u50d-02` | receiver | 18516 | 4.33 | 0.00 | 0.008 |
| `alveo-u50d-02` | receiver | 18517 | 4.32 | 0.00 | 0.008 |

- **Aggregate sender BW:** 13.86 Gb/s
- **Aggregate receiver BW:** 13.86 Gb/s

## Headline counter deltas

| Counter | u50d-02 | u50d-01 | u55c-03 | u55c-04 |
|---|---:|---:|---:|---:|
| `hw:np_cnp_sent` | 116,579 | — | — | — |
| `hw:rp_cnp_handled` | — | 41,681 | 95,203 | 95,514 |
| `hw:np_ecn_marked_roce_packets` | — | — | — | — |
| `hw:out_of_buffer` | — | — | — | — |
| `hw:out_of_sequence` | 133,863 | — | — | — |
| `hw:packet_seq_err` | — | 48,204 | 36,256 | 36,467 |
| `hw:local_ack_timeout_err` | — | — | 21,062 | — |
| `hw:implied_nak_seq_err` | — | — | — | — |
| `hw:rnr_nak_retry_err` | — | — | — | — |
| `ethtool:rx_ecn_mark` | — | — | — | — |
| `ethtool:rx_discards_phy` | — | — | — | — |
| `ethtool:rx_prio0_pause` | — | — | — | — |
| `ethtool:rx_prio3_pause` | — | — | — | — |
| `ethtool:tx_prio0_pause` | — | — | — | — |
| `ethtool:tx_prio3_pause` | — | — | — | — |
| `ib:port_rcv_errors` | — | — | — | — |
| `ib:port_xmit_discards` | — | — | — | — |
| `ib:port_xmit_data` | 46,504,296 | 119,455,503,134 | 86,892,494,153 | 91,432,962,033 |
| `ib:port_rcv_data` | 183,222,586,840 | 13,540,373 | 16,085,686 | 16,878,237 |

## Per-host details

<details>
<summary><strong>alveo-u50d-02</strong> (receiver, BlueField, PCC failed) — 80 other non-zero deltas</summary>

| Counter | Delta |
|---|---:|
| `ethtool:rx_bytes_phy` | 735,599,121,652 |
| `ethtool:rx_prio3_bytes` | 735,599,102,888 |
| `ethtool:rx_vport_rdma_unicast_bytes` | 732,890,347,358 |
| `ethtool:rx_packets_phy` | 677,191,564 |
| `ib:unicast_rcv_packets` | 677,191,497 |
| `ib:port_rcv_packets` | 677,191,497 |
| `ethtool:rx_vport_rdma_unicast_packets` | 677,191,497 |
| `ethtool:rx_prio3_packets` | 677,191,392 |
| `ethtool:rx_1024_to_1518_bytes_phy` | 677,191,383 |
| `ethtool:tx_bytes_phy` | 197,928,841 |
| `ethtool:tx_prio3_bytes` | 188,825,118 |
| `ethtool:tx_vport_rdma_unicast_bytes` | 186,017,184 |
| `ethtool:tx_prio6_bytes` | 9,093,490 |
| `ethtool:tx_packets_phy` | 2,977,648 |
| `ib:port_xmit_packets` | 2,977,644 |
| `ethtool:tx_vport_rdma_unicast_packets` | 2,977,644 |
| `ib:unicast_xmit_packets` | 2,977,644 |
| `ethtool:tx_prio3_packets` | 2,860,963 |
| `hw:rx_write_requests` | 1,670,126 |
| `ethtool:tx_prio6_packets` | 116,580 |
| `ethtool:rx_prio0_bytes` | 18,764 |
| `ethtool:tx_prio0_bytes` | 10,233 |
| `ethtool:rx_bytes` | 3,601 |
| `ethtool:rx_vport_broadcast_bytes` | 2,848 |
| `ethtool:rx27_bytes` | 1,648 |
| `ethtool:rx_vport_multicast_bytes` | 1,379 |
| `ethtool:rx0_bytes` | 753 |
| `ethtool:rx13_bytes` | 300 |
| `ethtool:rx11_bytes` | 300 |
| `ethtool:rx25_bytes` | 300 |
| `ethtool:rx4_bytes` | 300 |
| `ethtool:rx_prio0_packets` | 172 |
| `ethtool:rx_65_to_127_bytes_phy` | 136 |
| `ethtool:tx_prio0_packets` | 105 |
| `ethtool:rx_multicast_phy` | 43 |
| `ethtool:ch_events` | 28 |
| `ethtool:ch_poll` | 28 |
| `ethtool:ch_arm` | 28 |
| `ethtool:rx_packets` | 28 |
| `ethtool:rx_vport_broadcast_packets` | 25 |
| `ethtool:rx_256_to_511_bytes_phy` | 25 |
| `ethtool:rx_broadcast_phy` | 24 |
| `ethtool:rx_csum_unnecessary` | 20 |
| `ethtool:rx_64_bytes_phy` | 20 |
| `ethtool:rx_vport_multicast_packets` | 10 |
| `ethtool:rx_steer_missed_packets` | 7 |
| `ethtool:ch4_events` | 5 |
| `ethtool:ch13_events` | 5 |
| `ethtool:rx25_csum_unnecessary` | 5 |
| `ethtool:rx27_packets` | 5 |
| `ethtool:ch27_poll` | 5 |
| `ethtool:ch11_arm` | 5 |
| `ethtool:ch11_poll` | 5 |
| `ethtool:rx25_packets` | 5 |
| `ethtool:rx4_csum_unnecessary` | 5 |
| `ethtool:rx4_packets` | 5 |
| `ethtool:ch4_poll` | 5 |
| `ethtool:ch11_events` | 5 |
| `ethtool:ch4_arm` | 5 |
| `ethtool:ch13_poll` | 5 |
| `ethtool:rx11_csum_unnecessary` | 5 |
| `ethtool:ch25_arm` | 5 |
| `ethtool:ch25_events` | 5 |
| `ethtool:ch25_poll` | 5 |
| `ethtool:rx_csum_complete` | 5 |
| `ethtool:rx27_csum_complete` | 5 |
| `ethtool:rx11_packets` | 5 |
| `ethtool:ch27_events` | 5 |
| `ethtool:ch13_arm` | 5 |
| `ethtool:rx13_packets` | 5 |
| `ethtool:ch27_arm` | 5 |
| `ethtool:rx13_csum_unnecessary` | 5 |
| `ethtool:ch0_arm` | 3 |
| `ethtool:ch0_events` | 3 |
| `ethtool:rx0_csum_none` | 3 |
| `ethtool:rx0_packets` | 3 |
| `ethtool:tx_multicast_phy` | 3 |
| `ethtool:ch0_poll` | 3 |
| `ethtool:rx_csum_none` | 3 |
| `ethtool:tx_broadcast_phy` | 1 |

</details>

<details>
<summary><strong>alveo-u50d-01</strong> (sender, BlueField, PCC failed) — 76 other non-zero deltas</summary>

| Counter | Delta |
|---|---:|
| `ethtool:tx_bytes_phy` | 479,588,045,350 |
| `ethtool:tx_prio3_bytes` | 479,588,041,864 |
| `ethtool:tx_vport_rdma_unicast_bytes` | 477,822,012,538 |
| `ib:port_xmit_packets` | 441,508,203 |
| `ethtool:tx_vport_rdma_unicast_packets` | 441,508,203 |
| `ib:unicast_xmit_packets` | 441,508,203 |
| `ethtool:tx_packets_phy` | 441,508,203 |
| `ethtool:tx_prio3_packets` | 441,508,168 |
| `ethtool:rx_bytes_phy` | 57,632,383 |
| `ethtool:rx_prio3_bytes` | 54,369,142 |
| `ethtool:rx_vport_rdma_unicast_bytes` | 54,161,492 |
| `ethtool:rx_prio6_bytes` | 3,251,446 |
| `ethtool:rx_packets_phy` | 865,551 |
| `ethtool:rx_65_to_127_bytes_phy` | 865,518 |
| `ib:unicast_rcv_packets` | 865,482 |
| `ib:port_rcv_packets` | 865,482 |
| `ethtool:rx_vport_rdma_unicast_packets` | 865,482 |
| `ethtool:rx_prio3_packets` | 823,767 |
| `ethtool:rx_prio6_packets` | 41,682 |
| `hw:roce_slow_restart_cnps` | 14,688 |
| `hw:roce_adp_retrans` | 14,688 |
| `ethtool:rx_prio0_bytes` | 11,795 |
| `ethtool:rx_bytes` | 7,411 |
| `ethtool:rx_vport_multicast_bytes` | 5,839 |
| `ethtool:rx0_bytes` | 4,563 |
| `ethtool:tx_prio0_bytes` | 3,486 |
| `ethtool:rx_vport_broadcast_bytes` | 2,848 |
| `ethtool:rx12_bytes` | 1,648 |
| `ethtool:rx29_bytes` | 600 |
| `ethtool:rx16_bytes` | 300 |
| `ethtool:rx23_bytes` | 300 |
| `ethtool:rx_pp_recycle_cached` | 128 |
| `ethtool:rx_pp_alloc_fast` | 128 |
| `ethtool:rx0_pp_alloc_fast` | 128 |
| `ethtool:rx0_pp_recycle_cached` | 128 |
| `ethtool:rx_prio0_packets` | 102 |
| `ethtool:ch_events` | 61 |
| `ethtool:ch_poll` | 61 |
| `ethtool:ch_arm` | 60 |
| `ethtool:rx_packets` | 60 |
| `ethtool:rx_vport_multicast_packets` | 44 |
| `ethtool:rx_multicast_phy` | 44 |
| `ethtool:ch0_events` | 36 |
| `ethtool:ch0_poll` | 36 |
| `ethtool:tx_prio0_packets` | 35 |
| `ethtool:ch0_arm` | 35 |
| `ethtool:rx0_csum_none` | 35 |
| `ethtool:rx0_packets` | 35 |
| `ethtool:rx_csum_none` | 35 |
| `ethtool:rx_broadcast_phy` | 25 |
| `ethtool:rx_vport_broadcast_packets` | 25 |
| `ethtool:rx_csum_unnecessary` | 20 |
| `ethtool:rx_64_bytes_phy` | 20 |
| `ethtool:rx_256_to_511_bytes_phy` | 13 |
| `ethtool:ch29_arm` | 10 |
| `ethtool:ch29_poll` | 10 |
| `ethtool:rx29_packets` | 10 |
| `ethtool:rx29_csum_unnecessary` | 10 |
| `ethtool:ch29_events` | 10 |
| `ethtool:rx_steer_missed_packets` | 9 |
| `ethtool:ch16_events` | 5 |
| `ethtool:ch12_events` | 5 |
| `ethtool:rx23_csum_unnecessary` | 5 |
| `ethtool:rx12_packets` | 5 |
| `ethtool:ch23_events` | 5 |
| `ethtool:ch23_poll` | 5 |
| `ethtool:ch12_arm` | 5 |
| `ethtool:rx_csum_complete` | 5 |
| `ethtool:rx16_csum_unnecessary` | 5 |
| `ethtool:rx16_packets` | 5 |
| `ethtool:rx12_csum_complete` | 5 |
| `ethtool:ch16_poll` | 5 |
| `ethtool:ch12_poll` | 5 |
| `ethtool:ch23_arm` | 5 |
| `ethtool:ch16_arm` | 5 |
| `ethtool:rx23_packets` | 5 |

</details>

<details>
<summary><strong>alveo-u55c-03</strong> (sender, ConnectX) — 73 other non-zero deltas</summary>

| Counter | Delta |
|---|---:|
| `ethtool:tx_prio0_bytes` | 348,854,597,318 |
| `ethtool:tx_bytes_phy` | 348,854,597,318 |
| `ethtool:tx_vport_rdma_unicast_bytes` | 347,569,976,614 |
| `ethtool:tx_prio0_packets` | 321,155,116 |
| `ethtool:tx_packets_phy` | 321,155,116 |
| `ib:unicast_xmit_packets` | 321,155,113 |
| `ib:port_xmit_packets` | 321,155,113 |
| `ethtool:tx_vport_rdma_unicast_packets` | 321,155,113 |
| `ethtool:rx_prio0_bytes` | 68,473,535 |
| `ethtool:rx_bytes_phy` | 68,473,535 |
| `ethtool:rx_vport_rdma_unicast_bytes` | 64,342,744 |
| `ethtool:rx_prio0_packets` | 1,030,608 |
| `ethtool:rx_packets_phy` | 1,030,608 |
| `ethtool:rx_65_to_127_bytes_phy` | 1,030,576 |
| `ib:port_rcv_packets` | 1,030,540 |
| `ethtool:rx_vport_rdma_unicast_packets` | 1,030,540 |
| `ib:unicast_rcv_packets` | 1,030,540 |
| `hw:roce_slow_restart_cnps` | 21,062 |
| `hw:roce_adp_retrans` | 21,062 |
| `ethtool:rx_bytes` | 7,409 |
| `ethtool:rx_vport_multicast_bytes` | 5,511 |
| `ethtool:rx0_bytes` | 4,561 |
| `ethtool:rx_vport_broadcast_bytes` | 2,848 |
| `ethtool:rx2_bytes` | 1,648 |
| `ethtool:rx12_bytes` | 300 |
| `ethtool:rx24_bytes` | 300 |
| `ethtool:rx1_bytes` | 300 |
| `ethtool:rx6_bytes` | 300 |
| `ethtool:ch_events` | 60 |
| `ethtool:ch_poll` | 60 |
| `ethtool:rx_packets` | 60 |
| `ethtool:ch_arm` | 60 |
| `ethtool:rx_vport_multicast_packets` | 43 |
| `ethtool:rx_multicast_phy` | 43 |
| `ethtool:ch0_poll` | 35 |
| `ethtool:ch0_arm` | 35 |
| `ethtool:ch0_events` | 35 |
| `ethtool:rx0_csum_none` | 35 |
| `ethtool:rx0_packets` | 35 |
| `ethtool:rx_csum_none` | 35 |
| `ethtool:rx_broadcast_phy` | 25 |
| `ethtool:rx_vport_broadcast_packets` | 25 |
| `ethtool:rx_csum_unnecessary` | 20 |
| `ethtool:rx_64_bytes_phy` | 20 |
| `ethtool:rx_256_to_511_bytes_phy` | 12 |
| `ethtool:rx_steer_missed_packets` | 8 |
| `ethtool:rx24_packets` | 5 |
| `ethtool:rx1_csum_unnecessary` | 5 |
| `ethtool:ch12_events` | 5 |
| `ethtool:rx12_packets` | 5 |
| `ethtool:ch24_poll` | 5 |
| `ethtool:ch2_poll` | 5 |
| `ethtool:rx6_packets` | 5 |
| `ethtool:ch6_events` | 5 |
| `ethtool:ch6_poll` | 5 |
| `ethtool:ch1_events` | 5 |
| `ethtool:ch12_poll` | 5 |
| `ethtool:ch2_events` | 5 |
| `ethtool:ch6_arm` | 5 |
| `ethtool:ch24_arm` | 5 |
| `ethtool:rx2_csum_complete` | 5 |
| `ethtool:ch1_poll` | 5 |
| `ethtool:rx6_csum_unnecessary` | 5 |
| `ethtool:rx12_csum_unnecessary` | 5 |
| `ethtool:ch12_arm` | 5 |
| `ethtool:rx_csum_complete` | 5 |
| `ethtool:ch1_arm` | 5 |
| `ethtool:ch24_events` | 5 |
| `ethtool:rx24_csum_unnecessary` | 5 |
| `ethtool:rx2_packets` | 5 |
| `ethtool:rx1_packets` | 5 |
| `ethtool:ch2_arm` | 5 |
| `ethtool:tx_multicast_phy` | 3 |

</details>

<details>
<summary><strong>alveo-u55c-04</strong> (sender, ConnectX) — 74 other non-zero deltas</summary>

| Counter | Delta |
|---|---:|
| `ethtool:tx_prio0_bytes` | 367,083,595,194 |
| `ethtool:tx_bytes_phy` | 367,083,595,194 |
| `ethtool:tx_vport_rdma_unicast_bytes` | 365,731,848,130 |
| `ethtool:tx_prio0_packets` | 337,936,706 |
| `ethtool:tx_packets_phy` | 337,936,706 |
| `ib:unicast_xmit_packets` | 337,936,703 |
| `ib:port_xmit_packets` | 337,936,703 |
| `ethtool:tx_vport_rdma_unicast_packets` | 337,936,703 |
| `ethtool:rx_prio0_bytes` | 71,848,070 |
| `ethtool:rx_bytes_phy` | 71,848,070 |
| `ethtool:rx_vport_rdma_unicast_bytes` | 67,512,948 |
| `ethtool:rx_prio0_packets` | 1,081,690 |
| `ethtool:rx_packets_phy` | 1,081,690 |
| `ethtool:rx_65_to_127_bytes_phy` | 1,081,658 |
| `ib:port_rcv_packets` | 1,081,622 |
| `ethtool:rx_vport_rdma_unicast_packets` | 1,081,622 |
| `ib:unicast_rcv_packets` | 1,081,622 |
| `hw:roce_slow_restart_cnps` | 20,889 |
| `hw:roce_adp_retrans` | 20,889 |
| `ethtool:rx_bytes` | 7,411 |
| `ethtool:rx_vport_multicast_bytes` | 5,514 |
| `ethtool:rx0_bytes` | 4,563 |
| `ethtool:rx_vport_broadcast_bytes` | 2,848 |
| `ethtool:rx31_bytes` | 1,648 |
| `ethtool:rx7_bytes` | 300 |
| `ethtool:rx16_bytes` | 300 |
| `ethtool:rx23_bytes` | 300 |
| `ethtool:rx10_bytes` | 300 |
| `ethtool:ch_events` | 60 |
| `ethtool:ch_poll` | 60 |
| `ethtool:rx_packets` | 60 |
| `ethtool:ch_arm` | 60 |
| `ethtool:rx_vport_multicast_packets` | 43 |
| `ethtool:rx_multicast_phy` | 43 |
| `ethtool:ch0_poll` | 35 |
| `ethtool:ch0_arm` | 35 |
| `ethtool:ch0_events` | 35 |
| `ethtool:rx0_csum_none` | 35 |
| `ethtool:rx0_packets` | 35 |
| `ethtool:rx_csum_none` | 35 |
| `ethtool:rx_broadcast_phy` | 25 |
| `ethtool:rx_vport_broadcast_packets` | 25 |
| `ethtool:rx_csum_unnecessary` | 20 |
| `ethtool:rx_64_bytes_phy` | 20 |
| `ethtool:rx_256_to_511_bytes_phy` | 12 |
| `ethtool:rx_steer_missed_packets` | 8 |
| `ethtool:rx7_packets` | 5 |
| `ethtool:rx10_csum_unnecessary` | 5 |
| `ethtool:ch31_events` | 5 |
| `ethtool:ch16_poll` | 5 |
| `ethtool:rx31_csum_complete` | 5 |
| `ethtool:ch23_events` | 5 |
| `ethtool:ch16_events` | 5 |
| `ethtool:ch10_poll` | 5 |
| `ethtool:ch31_poll` | 5 |
| `ethtool:ch10_events` | 5 |
| `ethtool:ch23_arm` | 5 |
| `ethtool:ch23_poll` | 5 |
| `ethtool:ch10_arm` | 5 |
| `ethtool:ch16_arm` | 5 |
| `ethtool:rx23_csum_unnecessary` | 5 |
| `ethtool:ch7_arm` | 5 |
| `ethtool:ch31_arm` | 5 |
| `ethtool:rx7_csum_unnecessary` | 5 |
| `ethtool:rx_csum_complete` | 5 |
| `ethtool:rx31_packets` | 5 |
| `ethtool:ch7_poll` | 5 |
| `ethtool:rx10_packets` | 5 |
| `ethtool:rx23_packets` | 5 |
| `ethtool:rx16_csum_unnecessary` | 5 |
| `ethtool:rx16_packets` | 5 |
| `ethtool:ch7_events` | 5 |
| `ethtool:tx_multicast_phy` | 3 |
| `hw:roce_adp_retrans_to` | 2 |

</details>

## Failed commands

_None._
