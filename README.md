# WeeWX WMR200 Hardened Driver

Advanced USB driver for **Oregon Scientific WMR200 / WMR200A** consoles, designed for WeeWX 4/5 and Raspberry Pi deployments.

> **Driver version:** `3.5.4-gp10-archive-clock-recovery`  
> **Baseline:** `3.5.4-gp9-live-scheduler`  
> **Status:** community project; not officially supported by the WeeWX project.

## Why gp10

gp10 is a field-driven archive-recovery release. A real Raspberry Pi boot showed that WeeWX can start before networking/NTP has corrected the system clock. gp9 cached the first host/console drift and used wall-clock time inside startup recovery. A later NTP step could therefore terminate catch-up early and historical D2 records could be discarded or misclassified.

gp10 keeps the validated gp9 live USB scheduler and adds a clock-safe archive state machine:

- startup recovery quiet timers use **`time.monotonic()` only**;
- implausible first host/console drift samples are rejected;
- the driver keeps sampling until the host clock becomes plausible;
- after a configurable wait, recovery falls back to **native WMR200 timestamps** instead of applying a bogus multi-hour drift;
- archive ordering is based on consecutive D2 records, never on the WeeWX database watermark;
- `since_ts` is used only as the catch-up boundary;
- interrupted catch-up can resume from its original watermark using a small persistent state file;
- historical D2 logger cadence can be auto-detected independently of the 60-second live WeeWX archive interval.

## Preserved from gp9

- 2-second USB read slices with a 15-second logical communication timeout;
- D0 heartbeat latency tracing;
- state-aware D1/D2 handling in LIVE mode;
- checksum handling;
- EPIPE / timeout recovery and controlled reopen;
- malformed-HID stream resynchronization;
- sensor mappings and packet decoder;
- asynchronous rotating JSONL developer trace;
- asynchronous rotating textual driver log.

## Recommended configuration

```ini
[WMR200]
    model = WMR200
    driver = user.wmr200

    use_pc_time = True
    erase_archive = False
    archive_interval = 60
    archive_startup = 120
    archive_threshold = 1512000
    ignore_checksum = False
    sensor_status = True

    # gp10 archive / boot-clock hardening
    archive_clock_drift_max = 900
    archive_clock_wait = 180
    archive_recovery_resume = True
    archive_recovery_state_path = /var/lib/weewx/wmr200-archive-recovery.json
    archive_logger_interval = 0

    # USB recovery / gp9 scheduler retained by gp10
    usb_write_retries = 3
    usb_read_retries = 2
    usb_retry_delay = 0.5
    usb_reopen_on_failure = True
    usb_read_slice_timeout = 2.0
    usb_logical_timeout_seconds = 15
    usb_timeout_warn_consecutive = 2
    usb_timeout_error_consecutive = 4
    usb_health_interval = 300

    # Structured USB / protocol / archive trace
    developer_trace = True
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = True
    developer_trace_include_packets = True

    # Complete asynchronous driver text log
    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4

    [[sensor_map]]
```

`archive_logger_interval = 0` means auto-detect the interval of historical D2 records. It does **not** change the driver's `archive_interval = 60` exposed to WeeWX for normal live archiving.

## Important gp10 trace events

- `host_clock_not_ready`
- `host_clock_ready`
- `archive_clock_fallback_console_time`
- `archive_recovery_resume`
- `archive_recovery_state_cleared`
- `archive_logger_interval_detected`
- `archive_recovery_start`
- `archive_record_evaluated`
- `archive_recovery_complete`
- all gp9 USB scheduler events (`usb_poll_timeout`, `usb_read_timeout`, `heartbeat_sent`, etc.)

## Installation

```bash
sudo ./install.sh
```

or as a WeeWX extension:

```bash
sudo weectl extension install .
sudo weectl station reconfigure --driver=user.wmr200
```

See `INSTALLAZIONE-IT.md` and `UPGRADE-GP9-TO-GP10-IT.md` for the complete procedure.

## Safety

Keep:

```ini
erase_archive = False
```

during validation. gp10 does not intentionally erase the console logger during normal catch-up.
