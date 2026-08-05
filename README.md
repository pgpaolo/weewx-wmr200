# WeeWX WMR200 Hardened Driver

USB driver for **Oregon Scientific WMR200 / WMR200A** weather-station consoles, designed for **WeeWX 4 and WeeWX 5**.

This fork preserves the packet decoder and archive handling of the historical WMR200 driver while adding USB recovery, protocol-stream resynchronization, and non-blocking JSONL diagnostics.

> **Driver version:** `3.5.4-gp7-streamresync`  
> **Status:** community project; not officially supported by the WeeWX project.

## Main features

- live and archive packet acquisition from the WMR200 console;
- support for multi-channel temperature and humidity, pressure, wind, rain, UV, and sensor-status packets;
- bounded retry logic for USB reads and writes;
- recovery from `EPIPE`, endpoint stalls, and transient libusb errors;
- controlled release, rediscovery, and reopening of the USB device;
- detection of malformed HID reports;
- parser resynchronization after a discontinuity in the USB byte stream;
- isolated rejection of invalid-checksum packets without restarting WeeWX;
- USB health and consecutive-timeout monitoring;
- asynchronous, rotating JSONL developer trace, separate from the normal WeeWX log;
- controlled thread shutdown with a final diagnostic summary.

## Reference environment

The GP variant has primarily been used with:

- Debian 12 / Raspberry Pi OS;
- WeeWX 5.1.x;
- Python 3;
- an Oregon Scientific WMR200/WMR200A console connected through USB.

The extension layout retains compatibility with WeeWX 4, although the most recent changes have mainly been validated on WeeWX 5.

## Project origin

This project is derived from the historical WeeWX WMR200 driver, which was later separated from the main WeeWX distribution. Original copyright notices and contributor credits are preserved in the source code.

The `gp7-streamresync` variant adds USB hardening and advanced diagnostics. It is not an official WeeWX release.

## Installation

### 1. Back up the configuration

```bash
sudo cp -a /etc/weewx/weewx.conf \
  /etc/weewx/weewx.conf.$(date +%Y%m%d-%H%M%S).bak
```

The configuration path may be different for WeeWX installations created with `pip` or inside a virtual environment.

### 2. Stop WeeWX

```bash
sudo systemctl stop weewx
```

### 3. Install on WeeWX 5

From a local ZIP archive:

```bash
sudo weectl extension install ./weewx-wmr200.zip
```

Directly from GitHub, after replacing `<OWNER>` and `<REPOSITORY>`:

```bash
sudo weectl extension install \
  https://github.com/<OWNER>/<REPOSITORY>/archive/refs/heads/main.zip
```

Run the station reconfiguration wizard:

```bash
sudo weectl station reconfigure --driver=user.wmr200
```

### 4. Install on WeeWX 4

```bash
sudo wee_extension --install=./weewx-wmr200.zip
sudo wee_config --reconfigure --driver=user.wmr200 --no-prompt
```

### 5. Install the udev rule

The archive contains:

```text
util/udev/rules.d/wmr200.rules
```

Recommended manual installation:

```bash
sudo install -m 0644 util/udev/rules.d/wmr200.rules \
  /etc/udev/rules.d/60-wmr200.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

The rule grants access to USB device `0fde:ca01` and disables USB autosuspend for the console.

After applying the rule, disconnect and reconnect the WMR200, or reboot the host.

## Recommended configuration

Add or update the following sections in `weewx.conf`:

```ini
[Station]
    station_type = WMR200

[WMR200]
    model = WMR200
    driver = user.wmr200

    # Timestamps and archive handling
    use_pc_time = true
    erase_archive = false
    archive_interval = 60
    archive_startup = 120
    archive_threshold = 604800
    ignore_checksum = false
    sensor_status = true

    # USB recovery
    usb_write_retries = 3
    usb_read_retries = 2
    usb_retry_delay = 0.5
    usb_reopen_on_failure = true

    # USB timeout classification
    usb_timeout_warn_consecutive = 2
    usb_timeout_error_consecutive = 4
    usb_health_interval = 300

    # JSONL developer trace
    developer_trace = true
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 20
    developer_trace_backups = 5
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = true
    developer_trace_include_packets = true

    [[sensor_map]]
```

### Main configuration options

| Option | Default | Description |
|---|---:|---|
| `model` | `WMR200` | Model name displayed by WeeWX. |
| `use_pc_time` | `true` | Uses the computer clock instead of the console clock. |
| `erase_archive` | `false` | Erases the console's internal archive at startup. Use with caution. |
| `archive_interval` | `60` | Archive interval in seconds. Values `60` and `300` are considered validated by the driver. |
| `archive_startup` | `120` | Time without new archive packets before switching to live mode. |
| `archive_threshold` | `604800` | Maximum accepted difference between archive timestamps before an anomalous record is discarded. |
| `ignore_checksum` | `false` | Controls the error class used for a bad checksum; the invalid packet is still discarded without stopping the driver. |
| `sensor_status` | `true` | Writes sensor faults and status information to the normal WeeWX log. |
| `usb_write_retries` | `3` | Maximum number of attempts for a USB write. |
| `usb_read_retries` | `2` | Maximum number of retry attempts after a read-side pipe stall. |
| `usb_retry_delay` | `0.5` | Delay, in seconds, between USB retry attempts. |
| `usb_reopen_on_failure` | `true` | Reopens the device after repeated USB failures. |
| `usb_timeout_warn_consecutive` | `2` | Consecutive timeout count that changes health state to `warning`. |
| `usb_timeout_error_consecutive` | `4` | Consecutive timeout count that changes health state to `degraded`. |
| `usb_health_interval` | `300` | Minimum interval, in seconds, between periodic USB health snapshots. |
| `developer_trace` | `true` | Enables the structured developer trace. Disable it when diagnostics are not required. |
| `developer_trace_path` | `/var/log/weewx/wmr200-developer-trace.jsonl` | Preferred trace-file path. |
| `developer_trace_max_mb` | `20` | Maximum size of the active trace file before rotation. |
| `developer_trace_backups` | `5` | Number of rotated trace files retained. |
| `developer_trace_queue_size` | `4096` | Maximum number of pending trace records in the non-blocking writer queue. |
| `developer_trace_include_timeouts` | `true` | Includes individual USB timeout records. |
| `developer_trace_include_packets` | `true` | Includes complete and decoded packet records. |

## Developer trace

The JSONL trace is designed not to block the weather-acquisition loop:

- records are written by a dedicated thread;
- the queue is bounded;
- writer errors do not stop the driver;
- the file rotates automatically;
- if the configured path is not writable, the driver attempts the following fallback:

```text
/tmp/wmr200-developer-trace.jsonl
```

Full documentation:

- [`docs/DEVELOPER-TRACE.md`](docs/DEVELOPER-TRACE.md)

Quick checks:

```bash
sudo journalctl -u weewx -n 100 --no-pager | grep -i wmr200
sudo tail -f /var/log/weewx/wmr200-developer-trace.jsonl
```

## Post-installation verification

```bash
sudo systemctl restart weewx
sudo systemctl status weewx --no-pager
sudo journalctl -u weewx -n 150 --no-pager
```

The normal log should contain messages similar to:

```text
driver version is 3.5.4-gp7-streamresync
Opened WMR200 USB device VendorID=0x0fde ProductID=0xca01
WMR200 developer trace active at /var/log/weewx/wmr200-developer-trace.jsonl
```

Verify that the USB device is visible:

```bash
lsusb | grep -i '0fde:ca01'
```

Check the Python syntax before restarting WeeWX:

```bash
python3 -m py_compile /usr/share/weewx/user/wmr200.py
```

The installed driver path may vary depending on how WeeWX was installed.

## Updating

```bash
sudo systemctl stop weewx
sudo weectl extension install --yes ./weewx-wmr200.zip
sudo systemctl start weewx
```

Always check the command-line options supported by the installed WeeWX version:

```bash
weectl extension install --help
```

## Uninstalling

On WeeWX 5:

```bash
sudo weectl extension uninstall wmr200
```

Remove the udev rule manually only when it is no longer needed:

```bash
sudo rm -f /etc/udev/rules.d/60-wmr200.rules
sudo udevadm control --reload-rules
```

## Sensor mapping

Example: map `extraTemp1` and `extraHumid1` to the temperature/humidity sensor on channel 5:

```ini
[WMR200]
    [[sensor_map]]
        extraTemp1 = temperature_5
        extraHumid1 = humidity_5
```

Main default mapping:

| WeeWX field | WMR200 observation |
|---|---|
| `inTemp` | `temperature_0` |
| `outTemp` | `temperature_1` |
| `inHumidity` | `humidity_0` |
| `outHumidity` | `humidity_1` |
| `windSpeed` | `wind_speed` |
| `windDir` | `wind_dir` |
| `windGust` | `wind_gust` |
| `pressure` | `pressure` |
| `altimeter` | `altimeter` |
| `rain` | calculated by the driver from `rain_total` |
| `rainRate` | `rain_rate` |
| `UV` | `uv` |

Channels `temperature_2` through `temperature_8` and `humidity_2` through `humidity_8` are mapped to WeeWX extra fields.

## Basic troubleshooting

### USB device not found

```bash
lsusb
sudo journalctl -u weewx -b --no-pager | grep -Ei 'wmr200|usb|0fde|ca01'
```

Check:

- that device `0fde:ca01` is present;
- that the udev rule is installed and active;
- that the WeeWX service account has permission to access the device;
- that no other process has already claimed the USB interface.

### Trace file not created in `/var/log/weewx`

Check the normal WeeWX log:

```bash
sudo journalctl -u weewx -n 100 --no-pager | grep -i 'developer trace'
```

The driver explicitly reports whether it has switched to the `/tmp` fallback path.

### USB timeouts

A single `usb_read_timeout` does not prove that a weather packet was lost. The trace distinguishes between:

- an isolated informational timeout;
- a sequence of timeouts in `warning` state;
- a prolonged sequence in `degraded` state;
- the subsequent `usb_read_recovered` event.

See the dedicated trace guide for correct interpretation.

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).

## Credits

- Chris Manton — original driver;
- Lars de Bruin — packet-decoding contributions;
- John E.P. Hynes / HyTronix — upstream maintenance and USB fixes;
- Gianpaolo P. / pgpaolo - variant — USB hardening, JSONL diagnostics, and protocol-stream resynchronization.

## License and redistribution

The source code preserves the original copyright headers and refers to a `LICENSE.txt` file. Before publishing or redistributing this fork, include the applicable license file from the original project. Publishing source code without an applicable license does not automatically grant modification or redistribution rights.

## Disclaimer

This driver is provided without warranty. Test configuration changes on a non-production system whenever possible, and keep backups of `weewx.conf` and the weather database before upgrading a production station.
