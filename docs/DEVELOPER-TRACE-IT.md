# Diagnostica sviluppatore gp9

## Trace JSONL strutturato

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

Rotazione: 10 MB + 4 backup.

### Eventi scheduler USB gp9

- `usb_poll_timeout`: una singola finestra `interruptRead()` breve è scaduta;
  non equivale da sola a un guasto;
- `usb_read_timeout`: è stata superata una soglia logica di 15 s di silenzio;
- `usb_read_recovered`: i dati sono tornati dopo uno o più timeout logici;
- `usb_scheduler_config`: parametri effettivi del scheduler;
- `usb_control_write.lock_wait_s`: tempo atteso per acquisire il lock USB;
- `heartbeat_dispatch`: D0 pronto per l'invio;
- `heartbeat_sent`: latenza tra richiesta e invio del D0.

### Eventi stato LIVE / ARCHIVE

- `protocol_mode_change`
- `archive_ready_during_recovery`
- `archive_ready_while_live`
- `archive_data_while_live`
- `archive_record_dropped_while_live`
- `archive_queue_purged`

Durante `archive_recovery` D1 e D2 continuano il drenaggio storico. Durante
`live`/`live_pending` D1 non invia DA e D2 non viene mantenuto nella coda runtime.

### Eventi recupero archivio gp8/gp9

- `archive_recovery_start`
- `archive_recovery_waiting_for_time_drift`
- `archive_recovery_time_drift_ready`
- `archive_record_evaluated`
- `archive_recovery_gap`
- `archive_recovery_error`
- `archive_recovery_complete`

## Log testuale completo

```text
/var/log/weewx/wmr200-debug.log
```

Logger asincrono dedicato al driver. Rotazione: 10 MB + 4 backup.

## Riepilogo

```bash
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

## Pacchetto diagnostico

```bash
sudo ./collect-debug.sh --hours 24
```
