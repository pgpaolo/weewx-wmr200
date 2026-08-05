# WMR200 Developer Trace Guide

The developer trace records USB and protocol events produced by driver `3.5.4-gp7-streamresync` in **JSON Lines** (`.jsonl`) format.

Each line is an independent JSON object. The file can therefore be read while it is being written, filtered with `jq`, compressed, and analyzed without loading the entire trace into memory.

## 1. WeeWX log versus JSONL trace

The driver produces two separate diagnostic streams.

### Normal WeeWX log

Contains startup messages, errors, warnings, service state, and normal driver messages:

```bash
sudo journalctl -u weewx -f
```

### Developer trace

Contains structured USB and protocol events:

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

The trace is intended for detailed diagnostics. It does not replace the system journal.

## 2. Enabling the trace

Add the following options to the `[WMR200]` section of `weewx.conf`:

```ini
[WMR200]
    developer_trace = true
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 20
    developer_trace_backups = 5
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = true
    developer_trace_include_packets = true
```

Restart WeeWX:

```bash
sudo systemctl restart weewx
```

Verify that tracing is active:

```bash
sudo journalctl -u weewx -n 100 --no-pager \
  | grep -Ei 'WMR200 developer trace|trace path|fallback'
```

Expected message:

```text
WMR200 developer trace ENABLED: /var/log/weewx/wmr200-developer-trace.jsonl
```

The exact wording can vary slightly with the driver revision.

## 3. Directory permissions

Determine the effective service account:

```bash
systemctl show weewx -p User -p Group
```

On Debian-based installations, WeeWX normally runs under a dedicated account, although the local configuration may differ.

Example for user and group `weewx`:

```bash
sudo install -d -o weewx -g weewx -m 0750 /var/log/weewx
sudo touch /var/log/weewx/wmr200-developer-trace.jsonl
sudo chown weewx:weewx /var/log/weewx/wmr200-developer-trace.jsonl
sudo chmod 0640 /var/log/weewx/wmr200-developer-trace.jsonl
sudo systemctl restart weewx
```

If the configured path is not writable, the driver automatically attempts:

```text
/tmp/wmr200-developer-trace.jsonl
```

The path actually in use is always reported in the WeeWX journal.

## 4. Rotation

Trace rotation is handled internally by the driver.

With:

```ini
developer_trace_max_mb = 20
developer_trace_backups = 5
```

the following files can exist:

```text
wmr200-developer-trace.jsonl
wmr200-developer-trace.jsonl.1
wmr200-developer-trace.jsonl.2
wmr200-developer-trace.jsonl.3
wmr200-developer-trace.jsonl.4
wmr200-developer-trace.jsonl.5
```

The internal minimum size for the active file is 1 MiB. At least one backup is retained.

Do not configure a second `logrotate` policy for the same file unless the driver's internal rotation has been disabled. Two concurrent rotation mechanisms can produce inconsistent names or detach the currently open file.

## 5. Record structure

Common fields:

| Field | Meaning |
|---|---|
| `timestamp_utc` | ISO 8601 timestamp in UTC. |
| `elapsed_s` | Seconds elapsed since the trace session started. |
| `sequence` | Monotonic record number for the current session. |
| `thread` | Thread that generated the event. |
| `direction` | General category: `RX`, `TX`, `PACKET`, `EVENT`, or `HEALTH`. |
| `event` | Specific event name. |
| `severity` | Event severity when applicable, for example `INFO`, `WARNING`, or `ERROR`. |
| `length` | Raw-data length when raw data is included. |
| `hex` | Raw bytes in hexadecimal form when available. |

Simplified example:

```json
{"timestamp_utc":"2026-08-05T16:06:35.123+00:00","elapsed_s":120.451,"sequence":845,"thread":"WMR200UsbPoll","direction":"RX","event":"usb_interrupt_read","length":8,"hex":"07 d3 10 20 30 40 50 60"}
```

Fields vary by event. Consumers should tolerate additional fields and absent optional fields.

## 6. Event categories

### EVENT

| Event | Meaning |
|---|---|
| `trace_started` | JSONL writer started. |
| `trace_stopping` | Writer is stopping and draining the queue. |
| `driver_start` | Driver startup, including version and main configuration values. |
| `driver_stop` | Driver shutdown with summary counters. |
| `usb_open` | USB device found, opened, and interface claimed. |
| `usb_close` | USB interface released. |
| `usb_reopen_begin` | Device-reopen procedure started. |
| `usb_reopen_ok` | Device reopened successfully. |
| `usb_read_recovered` | First successful read after one or more timeouts. |

### RX

| Event | Meaning |
|---|---|
| `usb_interrupt_read` | HID interrupt report received successfully. |
| `usb_read_empty` | Read completed without a payload. |
| `usb_read_timeout` | USB polling timeout. This does not automatically mean that a sensor packet was lost. |
| `usb_read_pipe_stall` | Stall on the interrupt IN endpoint. |
| `usb_read_malformed` | HID byte count is incompatible with the available payload. |
| `usb_read_parse_error` | Error while interpreting the HID report. |
| `usb_read_error` | USB error not classified as a recoverable timeout or stall. |

### TX

| Event | Meaning |
|---|---|
| `protocol_command` | Protocol command sent to the console. |
| `protocol_reset` | Console reset/notification sequence. |
| `usb_control_write` | Result of a USB control transfer. |

### PACKET

| Event | Meaning |
|---|---|
| `protocol_packet_complete` | Complete packet before final validation. |
| `protocol_packet_decoded` | Valid packet converted into a driver record. |
| `protocol_packet_checksum_dropped` | Packet discarded because of an invalid checksum. |
| `protocol_packet_malformed_dropped` | Incomplete or structurally invalid packet discarded. |
| `protocol_packet_unhandled_error` | Unhandled exception during packet validation or processing. |
| `protocol_stream_resync` | Parser state reset after a byte-stream discontinuity. |

### HEALTH

| Event | Meaning |
|---|---|
| `usb_health_snapshot` | Periodic or forced snapshot of USB counters and health state. |

## 7. Interpreting timeouts

The USB read waits for a finite interval. If no report arrives during that interval, the driver produces `usb_read_timeout`.

The driver classifies consecutive timeouts as follows:

- `INFO` / `healthy`: isolated timeout;
- `WARNING` / `warning`: consecutive timeout count equal to or above `usb_timeout_warn_consecutive`;
- `ERROR` / `degraded`: consecutive timeout count equal to or above `usb_timeout_error_consecutive`.

Default thresholds:

```ini
usb_timeout_warn_consecutive = 2
usb_timeout_error_consecutive = 4
```

The next valid read produces `usb_read_recovered`, including the duration of the silent period and the number of consecutive timeouts recovered from.

An isolated timeout means that no USB report was received during one polling window. To establish actual data loss, correlate:

- consecutive timeout count;
- `seconds_since_last_success`;
- actual sensor packet intervals;
- any `stream_gap` indication;
- any `protocol_stream_resync` event;
- packet counters and application timestamps.

## 8. Live viewing

Without `jq`:

```bash
sudo tail -f /var/log/weewx/wmr200-developer-trace.jsonl
```

Pretty-printed JSON:

```bash
sudo tail -f /var/log/weewx/wmr200-developer-trace.jsonl \
  | jq --unbuffered .
```

Only records explicitly marked as warnings or errors:

```bash
sudo jq -c \
  'select(.severity == "WARNING" or .severity == "ERROR")' \
  /var/log/weewx/wmr200-developer-trace.jsonl
```

## 9. Useful `jq` filters

### Count records by event

```bash
sudo jq -r '.event' /var/log/weewx/wmr200-developer-trace.jsonl \
  | sort | uniq -c | sort -nr
```

### Critical USB events

```bash
sudo jq -c '
  select(.event == "usb_read_pipe_stall"
      or .event == "usb_read_error"
      or .event == "usb_read_malformed"
      or .event == "usb_reopen_begin"
      or .event == "usb_reopen_ok")
' /var/log/weewx/wmr200-developer-trace.jsonl
```

### Consecutive timeouts

```bash
sudo jq -c '
  select(.event == "usb_read_timeout")
  | {
      timestamp_utc,
      severity,
      timeout_consecutive,
      timeout_total,
      seconds_since_last_success,
      health_state
    }
' /var/log/weewx/wmr200-developer-trace.jsonl
```

### Protocol resynchronization events

```bash
sudo jq -c '
  select(.event == "protocol_stream_resync"
      or .event == "usb_read_malformed"
      or .event == "usb_read_parse_error")
' /var/log/weewx/wmr200-developer-trace.jsonl
```

### Checksum failures

```bash
sudo jq -c '
  select(.event == "protocol_packet_checksum_dropped")
  | {
      timestamp_utc,
      packet_id,
      packet_name,
      checksum_calculated,
      checksum_received,
      reason
    }
' /var/log/weewx/wmr200-developer-trace.jsonl
```

### Decoded packets by type

```bash
sudo jq -r '
  select(.event == "protocol_packet_decoded")
  | .packet_name
' /var/log/weewx/wmr200-developer-trace.jsonl \
  | sort | uniq -c | sort -nr
```

### Latest health snapshot

```bash
sudo jq -c '
  select(.event == "usb_health_snapshot")
' /var/log/weewx/wmr200-developer-trace.jsonl \
  | tail -n 1 | jq .
```

### Count parser resynchronizations

```bash
sudo jq -r '
  select(.event == "protocol_stream_resync") | .event
' /var/log/weewx/wmr200-developer-trace.jsonl \
  | wc -l
```

### Display the final driver summary

```bash
sudo jq -c '
  select(.event == "driver_stop")
' /var/log/weewx/wmr200-developer-trace.jsonl \
  | tail -n 1 | jq .
```

## 10. Checking JSONL integrity

This command should finish without an error:

```bash
sudo jq -e . /var/log/weewx/wmr200-developer-trace.jsonl \
  >/dev/null
```

Count invalid lines:

```bash
sudo awk '
  NF { print NR ":" $0 }
' /var/log/weewx/wmr200-developer-trace.jsonl \
  | while IFS=: read -r line payload; do
      printf '%s\n' "$payload" | jq -e . >/dev/null 2>&1 \
        || echo "Invalid JSON on line $line"
    done
```

For large files, prefer the first command because it parses the stream incrementally.

## 11. Reducing trace volume

### Exclude individual timeout records

```ini
developer_trace_include_timeouts = false
```

USB health snapshots and the other USB events remain available.

### Exclude complete and decoded packet records

```ini
developer_trace_include_packets = false
```

Transport, recovery, error, and health events remain available. This is the recommended configuration for long-running monitoring with lower disk usage.

Recommended configuration for continuous observation:

```ini
developer_trace = true
developer_trace_max_mb = 10
developer_trace_backups = 3
developer_trace_include_timeouts = false
developer_trace_include_packets = false
```

Recommended configuration for a full diagnostic session:

```ini
developer_trace = true
developer_trace_max_mb = 20
developer_trace_backups = 5
developer_trace_include_timeouts = true
developer_trace_include_packets = true
```

## 12. Disabling the trace

In `weewx.conf`:

```ini
developer_trace = false
```

Restart WeeWX:

```bash
sudo systemctl restart weewx
```

Disabling the trace does not delete existing trace files.

## 13. Collecting a diagnostic bundle

Create a temporary working directory:

```bash
WORKDIR="/tmp/wmr200-debug-$(date +%Y%m%d-%H%M%S)"
sudo mkdir -p "$WORKDIR"
```

Copy the active trace, rotated traces, and journal data:

```bash
sudo cp -a /var/log/weewx/wmr200-developer-trace.jsonl* "$WORKDIR"/ 2>/dev/null || true
sudo journalctl -u weewx --since '-24 hours' --no-pager \
  > "$WORKDIR/weewx-journal.txt"
lsusb > "$WORKDIR/lsusb.txt"
uname -a > "$WORKDIR/uname.txt"
python3 --version > "$WORKDIR/python-version.txt" 2>&1
weectl --version > "$WORKDIR/weewx-version.txt" 2>&1 || true
```

Add a sanitized copy of the `[WMR200]` configuration section only. Do not include passwords, tokens, unrelated services, or private configuration values.

Create the archive:

```bash
sudo tar -C "$(dirname "$WORKDIR")" -czf "${WORKDIR}.tar.gz" \
  "$(basename "$WORKDIR")"
sudo chown "$(id -u):$(id -g)" "${WORKDIR}.tar.gz"
```

## 14. Checks before sharing a trace

The trace can contain:

- precise timestamps;
- decoded weather observations when `developer_trace_include_packets = true`;
- raw bytes received from and sent to the console;
- device and system details useful for diagnostics.

It does not normally contain passwords or tokens, but scan the bundle before publishing it:

```bash
grep -RIniE \
  'password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization' \
  "$WORKDIR"
```

Also check for hostnames, IP addresses, station identifiers, and location data in the journal or attached configuration.

## 15. Quick diagnostic patterns

### Case A — isolated timeouts, no stream gap

Typical indicators:

- a small number of `usb_read_timeout` events;
- `timeout_consecutive = 1`;
- no stream-gap indication;
- no `protocol_stream_resync` event;
- sensor packets received at plausible intervals.

Assessment: normally acceptable behavior.

### Case B — consecutive timeouts followed by recovery

Typical indicators:

- `warning` or `degraded` health state;
- a later `usb_read_recovered` event;
- no malformed HID report.

Assessment: temporary USB interruption or a temporarily silent console. Check frequency and duration.

### Case C — malformed HID report followed by resynchronization

Typical indicators:

- `usb_read_malformed` or `usb_read_parse_error`;
- incremented `stream_gap_count`;
- `protocol_stream_resync`;
- correctly decoded packets after resynchronization.

Assessment: an actual stream discontinuity was handled by the driver. If this occurs frequently, inspect power quality, the USB cable, hubs, and libusb stability.

### Case D — pipe stall and device reopen

Typical indicators:

- `usb_read_pipe_stall`, or `usb_control_write` with `status=error`;
- `usb_reopen_begin`;
- `usb_reopen_ok`;
- reads resume afterward.

Assessment: recovery succeeded. If the event repeats, investigate power, USB autosuspend, cable quality, hubs, and competing access to the USB device.

### Case E — unhandled packet exception

Indicator:

```text
protocol_packet_unhandled_error
```

Assessment: attach the trace, WeeWX journal, and exact driver version to the issue report. This event has the highest priority for a code fix.

## 16. Suggested issue-report content

Include:

- driver version;
- WeeWX version;
- Python version;
- operating system and architecture;
- whether the console is connected directly or through a USB hub;
- the relevant time window and timezone;
- a sanitized trace excerpt or diagnostic bundle;
- the exact symptoms observed in WeeWX.

Do not publish an entire long-running trace when a short, clearly identified time window is sufficient.
