# WeeWX WMR200 Hardened Driver

Advanced USB driver for **Oregon Scientific WMR200 / WMR200A** weather-station consoles, designed for **WeeWX 4 and WeeWX 5**.

This fork preserves the packet decoder and archive handling of the historical WMR200 driver while adding USB recovery, protocol-stream resynchronization, non-blocking diagnostics, and startup archive-recovery tracing.

> **Driver version:** `3.5.4-gp8-archive-trace`  
> **Baseline:** `3.5.4-gp7-streamresync`  
> **Status:** community project; not officially supported by the WeeWX project.

## Highlights

- bounded recovery for USB timeout / EPIPE conditions;
- controlled USB handle reopen after repeated transfer failures;
- protocol stream resynchronization after malformed HID reports;
- checksum failures drop the affected packet without restarting WeeWX;
- structured JSONL RX/TX and protocol developer trace;
- startup archive-recovery diagnostics with gap, duplicate and out-of-order detection;
- asynchronous rotating textual driver log;
- diagnostic logging isolated from weather acquisition;
- WMR200 archive preserved by default (`erase_archive = False`).

## Repository layout

```text
bin/user/wmr200.py
                    Main WeeWX driver

docs/
                    Installation, diagnostics, testing and upgrade notes

util/udev/rules.d/
                    Linux udev rule for WMR200 USB access and autosuspend

install.py
                    WeeWX ExtensionInstaller definition

README.md
CHANGELOG.md
changelog
```

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

    # USB recovery
    usb_write_retries = 3
    usb_read_retries = 2
    usb_retry_delay = 0.5
    usb_reopen_on_failure = True

    # Structured USB / protocol trace
    developer_trace = True
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = True
    developer_trace_include_packets = True

    # Complete textual driver log
    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4

    [[sensor_map]]
```

Each diagnostic log family is bounded to **one active file plus four backups**: 5 files maximum, approximately 50 MB per family with the defaults above.

## Installation — upgrade from an existing WMR200 driver

From a clone/download of this repository:

```bash
sudo systemctl stop weewx
sudo cp bin/user/wmr200.py /etc/weewx/bin/user/wmr200.py
sudo mkdir -p /var/log/weewx
sudo chown weewx:weewx /var/log/weewx
sudo systemctl start weewx
```

The exact WeeWX user-extension directory can differ according to the installation method. For WeeWX 5 package installations, use the path shown by your existing driver installation.

## Installation as a WeeWX extension

```bash
sudo weectl extension install .
sudo weectl station reconfigure --driver=user.wmr200
```

Install the supplied udev rule if required:

```bash
sudo cp util/udev/rules.d/wmr200.rules /etc/udev/rules.d/99-wmr200.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Reconnect the WMR200 USB cable, or reboot the host, after changing udev rules.

See [docs/INSTALLAZIONE-IT.md](docs/INSTALLAZIONE-IT.md) for the complete procedure.

## Diagnostics

Structured trace:

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

Text driver log:

```text
/var/log/weewx/wmr200-debug.log
```

The gp8 trace adds archive events such as:

- `archive_recovery_start`
- `archive_recovery_record`
- `archive_recovery_gap`
- `archive_recovery_complete`

This makes it possible to verify recovery of historical records after WeeWX has been offline while the WMR200 console continued logging locally.

## Documentation

- [Installation (Italian)](docs/INSTALLAZIONE-IT.md)
- [Developer trace (Italian)](docs/DEVELOPER-TRACE-IT.md)
- [Testing (Italian)](docs/TESTING-IT.md)
- [Upgrade gp7 → gp8 (Italian)](docs/UPGRADE-GP7-TO-GP8-IT.md)
- [Changelog](CHANGELOG.md)

## Safety

The recommended configuration keeps:

```ini
erase_archive = False
```

so the console archive is not intentionally erased during normal startup recovery.

Diagnostic writers are best-effort and asynchronous. Logging failures are designed not to propagate into the weather-acquisition path.
