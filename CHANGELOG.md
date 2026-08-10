# Changelog

## 3.5.4-gp10-archive-clock-recovery

Baseline: **3.5.4-gp9-live-scheduler**.

### Fixed

- Startup archive timeout is now measured with `time.monotonic()` so NTP/system-clock steps cannot expire catch-up early.
- A multi-hour host/console drift captured before Raspberry Pi NTP synchronization is no longer accepted blindly.
- Archive D2 records older than WeeWX `since_ts` are no longer misclassified as `out_of_order`; the database watermark and console sequence are separate concepts.
- A sub-minute malformed archive timestamp no longer advances the archive sequence reference.
- Interrupted startup recovery can retain its original catch-up watermark across a WeeWX driver restart.

### Added

- Configurable `archive_clock_drift_max` (default 900 s).
- Configurable `archive_clock_wait` (default 180 s).
- Persistent catch-up state with `archive_recovery_resume` and `archive_recovery_state_path`.
- `archive_logger_interval = 0` auto-detection for historical D2 cadence, independent of the live `archive_interval`.
- Clock/recovery trace events: `host_clock_not_ready`, `host_clock_ready`, `archive_clock_fallback_console_time`, `archive_recovery_resume`, `archive_recovery_state_cleared`, `archive_logger_interval_detected`.
- Regression tests based on the observed 10-Aug-2026 failure pattern (large boot drift followed by NTP correction and older D2 records).

### Preserved from gp9

- 2-second USB read slices and 15-second logical timeout semantics.
- Heartbeat scheduler and timing trace.
- LIVE/ARCHIVE D1/D2 state handling.
- Decoder, checksum handling, EPIPE/reopen and stream resynchronization.
- Bounded asynchronous developer and text logs.

## 3.5.4-gp9-live-scheduler

Introduced short USB read slices, heartbeat latency diagnostics and mode-aware D1/D2 handling.

## 3.5.4-gp8-archive-trace

Added detailed archive-recovery tracing, gap accounting and asynchronous rotating driver logging.
