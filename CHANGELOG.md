# Changelog

All notable changes to this project are documented in this file.

The format follows the principles of **Keep a Changelog**. The GP variant uses a version derived from the historical WMR200 branch and is not an official WeeWX release.

## [Unreleased]

### Required before public release

- add the license file applicable to the original source code;
- replace the GitHub placeholders `<OWNER>` and `<REPOSITORY>` in the documentation;
- add automated replay tests using anonymized USB traces, when suitable traces are available;
- consider disabling the developer trace by default in non-diagnostic releases.

## [3.5.4-gp7-streamresync] - 2026-08-05

### Added

- non-blocking JSONL developer trace with a dedicated writer thread;
- configurable queue and counters for `records_written`, `records_dropped`, and `writer_errors`;
- internal size-based trace rotation with configurable backup retention;
- automatic fallback to `/tmp/wmr200-developer-trace.jsonl` when the configured path is not writable;
- structured RX, TX, PACKET, EVENT, and HEALTH records;
- periodic USB health snapshots;
- timeout classification as `healthy`, `warning`, or `degraded`;
- `usb_read_recovered` event after successful reception resumes;
- counters for successful reads, timeouts, bursts, pipe stalls, malformed reports, device reopen operations, and stream gaps;
- configurable USB retries, automatic reopen behavior, and timeout thresholds;
- internal USB-stream discontinuity marker;
- `protocol_stream_resync` event for parser resynchronization;
- detailed events for complete, decoded, discarded, and malformed packets;
- final diagnostic summary when the driver stops.

### Changed

- serialized USB access through a recursive lock to prevent concurrent reads and writes on the same handle;
- made USB opening idempotent and reusable during recovery;
- retained vendor ID and product ID for device rediscovery and reopening;
- treated read timeouts as normally recoverable conditions;
- handled `EPIPE` and pipe stalls with `clearHalt()` on the interrupt IN endpoint only;
- performed USB writes with bounded retries and one additional attempt after reopening the device;
- discarded HID reports whose declared byte count exceeds the available payload and marked the stream for resynchronization;
- limited checksum failures to the affected packet instead of restarting the driver or WeeWX engine;
- isolated incomplete or malformed packets from subsequent stream data;
- made driver shutdown more robust when the USB console is no longer available.

### Fixed

- permanent parser desynchronization after an incomplete or malformed HID report;
- propagation of recoverable checksum errors to the WeeWX engine;
- possible conflicts between `interruptRead()` and `controlMsg()` from different threads;
- failure to reopen the USB device after repeated transient errors;
- risk that a trace-writer error could stop weather acquisition;
- insufficient diagnostics for distinguishing a polling timeout from an actual byte-stream discontinuity.

### Validation

- Python syntax checked with `python3 -m py_compile`;
- reference trace analyzed without malformed JSONL records;
- no stream gaps, resynchronizations, checksum errors, pipe stalls, reopen operations, or unhandled exceptions were observed during the GP7 validation session.

## [3.5.4-gp2] - 2026-08-03

### Added

- initial RX/TX developer trace in JSONL format;
- initial options for trace path, maximum size, backup count, and packet inclusion;
- USB recovery statistics and timeout counters;
- diagnostic support for analyzing packets received from and sent to the console.

### Changed

- made USB retry counts configurable;
- added the ability to reopen the device after a persistent error;
- improved separation between the operational WeeWX log and the development trace.

## [3.5.3-hytronix] - 2026-06-26

### Fixed

- added exception handling in `write_device()` for the historical USB communication problem where no retry, endpoint clear, or reset occurred after a pipe stall;
- released the USB device in `open_device()` while recovering from exceptions;
- updated the udev rule to disable USB autosuspend.

## [3.5.2] - 2021-02-18

### Fixed

- made driver restart search for the device again on the USB bus;
- fixed the historical problem documented as issue `#1` in the upstream repository.

## [3.5.1] - 2021-01-04

### Added

- included udev rules in the extension repository.

## [3.5.0] - 2020-12-15

### Changed

- separated the WMR200 driver from the main WeeWX distribution;
- published it as a standalone, officially unsupported extension.
