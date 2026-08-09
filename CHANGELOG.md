# Changelog

## 3.5.4-gp9-live-scheduler

Baseline: **3.5.4-gp8-archive-trace**.

### Added

- 2-second interrupt-read scheduling slices to release the shared PyUSB lock frequently.
- Separate 15-second logical communication-timeout accounting.
- Heartbeat request-age and dispatch timing trace events.
- USB control-write lock-wait measurements.
- Explicit protocol modes: `initializing`, `archive_recovery`, `live_pending`, `live`.
- Mode-aware D1/D2 archive handling.
- Runtime archive-queue protection during LIVE mode.
- `usb_scheduler_config`, `heartbeat_dispatch`, `heartbeat_sent`,
  `archive_ready_while_live`, `archive_data_while_live`, and
  `archive_record_dropped_while_live` trace events.

### Changed

- A 2-second `interruptRead()` timeout is now a scheduling event
  (`usb_poll_timeout`) and does not by itself increment communication-health timeout counters.
- `usb_read_timeout` is emitted only after each 15-second continuous-silence boundary.
- D1 requests D2 only while startup archive recovery is active.
- D2 received during LIVE no longer requests another archive record and is not retained in `PacketArchive.pkt_queue`.
- Static live/archive packet queues are cleared when a new driver instance starts.

### Preserved from gp8

- Decoder and sensor maps.
- Checksum handling.
- EPIPE / timeout retry and controlled reopen.
- Malformed HID stream resynchronization.
- Startup archive recovery diagnostics.
- Asynchronous JSONL and text logging with bounded rotation.

## 3.5.4-gp8-archive-trace

Added detailed archive-recovery tracing, gap accounting, and asynchronous
rotating driver-file logging on top of gp7-streamresync.
