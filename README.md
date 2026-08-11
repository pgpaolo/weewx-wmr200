# WeeWX WMR200 Hardened Driver

[![CI](https://github.com/pgpaolo/weewx-wmr200/actions/workflows/validate.yml/badge.svg)](https://github.com/pgpaolo/weewx-wmr200/actions/workflows/validate.yml)
![Status](https://img.shields.io/badge/status-release%20candidate-orange)
![Driver](https://img.shields.io/badge/driver-3.5.4--gp10-blue)
![WeeWX](https://img.shields.io/badge/WeeWX-4%20%7C%205-4c8bf5)
![Hardware](https://img.shields.io/badge/hardware-WMR200%20%7C%20WMR200A-lightgrey)

Advanced USB driver for **Oregon Scientific WMR200 / WMR200A** consoles, with hardened USB handling, structured diagnostics, developer tracing and archive catch-up support for Raspberry Pi / Linux WeeWX deployments.

> **Driver version:** `3.5.4-gp10-archive-clock-recovery`  
> **Baseline:** `3.5.4-gp9-live-scheduler`  
> **Status:** release candidate / field validation  
> **Support:** community project; not officially supported by the WeeWX project

## Project lineage

This repository is derived from the WMR200 driver maintained in the WeeWX community and preserves the original source attribution. The upstream historical repository is:

- [weewx/weewx-wmr200](https://github.com/weewx/weewx-wmr200)

The gp-series work focuses on USB resilience, diagnostics, stream recovery and safe archive catch-up while preserving the established WMR200 packet decoder and sensor mappings.

## Why gp10

`gp10` is a field-driven archive-recovery release. A real Raspberry Pi boot showed that WeeWX can start before networking/NTP has corrected the system clock. In that situation, a wall-clock step could terminate archive catch-up early and historical D2 records could later be rejected against the current database tail.

`gp10` keeps the validated gp9 live USB scheduler and adds a clock-safe archive state machine:

- startup recovery quiet timers use `time.monotonic()`;
- implausible initial host/console drift samples are rejected;
- the driver keeps sampling until the host clock becomes plausible;
- after a configurable wait, recovery can fall back to native WMR200 timestamps instead of applying a bogus multi-hour drift;
- archive ordering is based on consecutive D2 records, not on the WeeWX database watermark;
- `since_ts` is used only as the catch-up boundary;
- interrupted catch-up can resume from its original watermark using a persistent state file;
- historical D2 cadence can be auto-detected independently of the normal live WeeWX archive interval.

## Preserved from gp9

- 2-second USB read slices with a 15-second logical communication timeout;
- D0 heartbeat latency tracing;
- state-aware D1/D2 handling in LIVE mode;
- checksum handling;
- EPIPE / timeout recovery and controlled reopen;
- malformed-HID stream resynchronization;
- existing sensor mappings and packet decoder;
- asynchronous rotating JSONL developer trace;
- asynchronous rotating textual driver log.

## Installation — WeeWX 5

The repository is a standard WeeWX extension because it contains `install.py`. The official WeeWX 5 extension command is `weectl extension install EXTENSION-LOCATION`.

### From a local clone

```bash
git clone https://github.com/pgpaolo/weewx-wmr200.git
cd weewx-wmr200

sudo weectl extension install .
sudo weectl station reconfigure --driver=user.wmr200
```

Install the supplied udev rule once on Linux systems where the WeeWX service needs direct USB access:

```bash
sudo install -m 0644 util/udev/rules.d/wmr200.rules /etc/udev/rules.d/wmr200.rules
sudo udevadm control --reload-rules
```

Then unplug/replug the WMR200 USB connection, or reboot the host, and restart WeeWX:

```bash
sudo systemctl restart weewx
```

> On installations where WeeWX is owned by the current user rather than root, `sudo` may not be required for the `weectl` commands.

See the full Italian installation guide: [docs/INSTALLAZIONE-IT.md](docs/INSTALLAZIONE-IT.md).

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

    # gp9 USB scheduler retained by gp10
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

`archive_logger_interval = 0` enables auto-detection of the cadence of historical D2 records. It does **not** change `archive_interval = 60` exposed to WeeWX for normal live archiving.

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
- gp9 USB scheduler events such as `usb_poll_timeout`, `usb_read_timeout` and `heartbeat_sent`

## Diagnostics

When reporting a problem, include the driver version, WeeWX version, host OS / Raspberry Pi model, WMR200/WMR200A model, the relevant `[WMR200]` configuration with secrets removed, and preferably a diagnostic bundle or the developer trace around the incident.

See:

- [Developer trace](docs/DEVELOPER-TRACE.md)
- [Developer trace — Italian](docs/DEVELOPER-TRACE-IT.md)
- [Testing — Italian](docs/TESTING-IT.md)
- [Archive recovery / NTP — Italian](docs/ARCHIVE-RECOVERY-NTP-IT.md)

## Upgrade and release notes

- [gp9 → gp10 upgrade guide](docs/UPGRADE-GP9-TO-GP10-IT.md)
- [gp10 release notes](docs/RELEASE-NOTES-GP10.md)
- [Changelog](CHANGELOG.md)

## Safety

During validation keep:

```ini
erase_archive = False
```

The driver does not intentionally erase the console logger during normal catch-up when this option is disabled.

## Contributing

Bug reports and hardware test results are welcome. Please use the GitHub issue templates so that USB, archive and clock information is collected consistently.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

For security-related reports, see [SECURITY.md](SECURITY.md). Do not publish credentials, private hostnames, API keys or complete configuration files containing secrets in public issues.
