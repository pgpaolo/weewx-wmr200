# Diagnostica sviluppatore gp10

File JSONL:

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

## Eventi clock / NTP

- `host_clock_not_ready`: drift host-console troppo grande; gp10 non lo applica.
- `host_clock_ready`: campione successivo plausibile, tipicamente dopo NTP.
- `archive_clock_fallback_console_time`: NTP non arriva entro `archive_clock_wait`; gp10 usa il timestamp nativo WMR200 con drift zero.

## Eventi catch-up

- `archive_recovery_start`
- `archive_recovery_resume`
- `archive_recovery_waiting_for_time_drift`
- `archive_recovery_time_drift_ready`
- `archive_logger_interval_detected`
- `archive_record_evaluated`
- `archive_recovery_gap`
- `archive_recovery_complete`
- `archive_recovery_state_cleared`

`archive_record_evaluated` contiene sia `requested_since_ts` (ultimo record che WeeWX vede nel DB) sia `effective_since_ts` (watermark realmente usato, eventualmente ripreso dallo stato persistente).

## Eventi USB ereditati da gp9

- `usb_poll_timeout`: slice breve di scheduling, informativa.
- `usb_read_timeout`: vero timeout logico dopo silenzio continuo.
- `heartbeat_dispatch`, `heartbeat_sent`
- `archive_ready_while_live`
- `archive_data_while_live`
- `archive_record_dropped_while_live`

## Filtri utili

```bash
grep -E 'host_clock_|archive_clock_|archive_recovery|archive_record_evaluated|archive_logger_interval' /var/log/weewx/wmr200-developer-trace.jsonl
```

Per il caso Raspberry senza ora NTP al boot, la sequenza ideale è:

```text
host_clock_not_ready
... NTP sincronizza il Raspberry ...
host_clock_ready
archive_record_evaluated disposition=yielded
...
archive_recovery_complete outcome=archive_drained
archive_recovery_state_cleared
```
