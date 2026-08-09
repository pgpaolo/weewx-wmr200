# Changelog

## 3.5.4-gp8-archive-trace

Baseline: **3.5.4-gp7-streamresync**.

### Added

- Structured startup archive-recovery tracing.
- Archive record classification: yielded, before requested timestamp, duplicate, out-of-order, threshold-exceeded and sub-minute.
- Archive gap detection and accounting.
- Final archive-recovery summary with counters, first/last timestamps and elapsed time.
- Real asynchronous rotating `driver_file_log`.
- Separate structured JSONL developer trace and textual DEBUG driver log.
- Bounded diagnostic rotation: 10 MB per file, 4 backups by default.

### Changed

- A sub-minute malformed archive interval is dropped without aborting the complete startup recovery.
- Documentation and default configuration updated for archive diagnostics.
- `erase_archive = False` remains the recommended default.

### Preserved from gp7-streamresync

- USB timeout classification and recovery.
- EPIPE / interrupt endpoint stall handling.
- Controlled USB reopen after repeated failures.
- Protocol stream resynchronization after malformed HID reports.
- Recoverable checksum packet drops.
- Non-blocking structured developer tracing.

## 3.5.4-gp7-streamresync

Previous hardened baseline. Added protocol stream resynchronization and USB health/recovery diagnostics.
