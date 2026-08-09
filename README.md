# WeeWX WMR200 Hardened Driver

Advanced USB driver for **Oregon Scientific WMR200 / WMR200A** weather-station consoles, designed for **WeeWX 4 and WeeWX 5**.

> **Driver version:** `3.5.4-gp9-live-scheduler`  
> **Baseline:** `3.5.4-gp8-archive-trace`  
> **Status:** community project; not officially supported by the WeeWX project.

## Why gp9

A gp8 diagnostic trace showed that the shared PyUSB lock could be held by a
15-second blocking `interruptRead()`, delaying D0 live heartbeats by many
seconds. gp9 keeps the gp8 recovery/parser logic but changes the USB scheduler:

- `interruptRead()` runs in **2-second slices**;
- a slice timeout is only `usb_poll_timeout`, not a communication fault;
- health still counts one logical `usb_read_timeout` every **15 seconds of continuous silence**;
- D0 request age, command elapsed time, and USB lock wait are traced;
- D1/D2 archive commands are state-aware;
- archive drain continues only during `genStartupRecords()`;
- D1/D2 received during normal LIVE mode do not start a new archive-drain chain;
- late D2 records in LIVE are traced and dropped from the runtime archive queue.

## Preserved from gp8

- WMR200 packet decoder and sensor mappings;
- checksum verification and recoverable packet drop;
- EPIPE / timeout recovery;
- controlled USB handle reopen;
- malformed-HID protocol stream resynchronization;
- structured JSONL developer trace;
- asynchronous rotating textual driver log;
- startup archive-recovery diagnostics and gap accounting.

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

    # USB recovery / gp9 scheduler
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

    # Complete asynchronous textual driver log
    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4

    [[sensor_map]]
```

Each diagnostic family is bounded to **one active file plus four backups**:
5 files maximum, approximately 50 MB per family with the defaults above.

## Repository layout

```text
bin/user/wmr200.py
                    Main WeeWX driver

docs/
                    Installation, diagnostics, testing and upgrade notes

util/udev/rules.d/
                    Linux udev rule for WMR200 USB access/autosuspend

install.py
                    WeeWX ExtensionInstaller definition

README.md
CHANGELOG.md
changelog
```

## Install as a WeeWX extension

```bash
sudo weectl extension install .
sudo weectl station reconfigure --driver=user.wmr200
```

Install the supplied udev rule when required:

```bash
sudo cp util/udev/rules.d/wmr200.rules /etc/udev/rules.d/99-wmr200.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

See [docs/INSTALLAZIONE-IT.md](docs/INSTALLAZIONE-IT.md) for the complete
procedure.

## Diagnostics

Structured trace:

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

Text driver log:

```text
/var/log/weewx/wmr200-debug.log
```

Important gp9 events include:

- `usb_scheduler_config`
- `usb_poll_timeout`
- `usb_read_timeout`
- `heartbeat_dispatch`
- `heartbeat_sent`
- `protocol_mode_change`
- `archive_ready_while_live`
- `archive_data_while_live`
- `archive_record_dropped_while_live`
- `archive_recovery_complete`

## Safety

The recommended configuration keeps:

```ini
erase_archive = False
```

so the console archive is not intentionally erased during normal startup
recovery. Diagnostic writers are asynchronous and best-effort so log failures
are designed not to propagate into weather acquisition.
