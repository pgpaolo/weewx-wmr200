# Diagnostica sviluppatore gp8

Sono disponibili due log indipendenti.

## 1. Trace JSONL strutturato

```text
/var/log/weewx/wmr200-developer-trace.jsonl
```

Contiene eventi USB RX/TX, pacchetti protocollo, checksum, recovery, health e
sincronizzazione archivio. Rotazione predefinita: 10 MB + 4 backup.

Eventi archivio gp8 principali:

- `archive_recovery_start`
- `archive_recovery_waiting_for_time_drift`
- `archive_recovery_time_drift_ready`
- `archive_record_evaluated`
- `archive_recovery_gap`
- `archive_recovery_error`
- `archive_recovery_complete`

`archive_record_evaluated.disposition` può essere:

- `yielded`
- `before_since_ts`
- `duplicate`
- `out_of_order`
- `threshold_exceeded`
- `subminute_interval`

Il riepilogo finale contiene numero di record ricevuti/consegnati, scarti,
gap temporali, primo/ultimo record, durata reale del recupero e coda residua.

## 2. Log testuale completo del driver

```text
/var/log/weewx/wmr200-debug.log
```

È un logger asincrono dedicato al modulo `user.wmr200`. Con livello `DEBUG`
raccoglie i messaggi del driver senza obbligare il normale syslog/journal a
ricevere tutto il DEBUG. Anche questo log usa 10 MB + 4 backup.

## Attivazione/disattivazione

```bash
sudo ./enable-developer-debug.sh
sudo ./disable-developer-debug.sh
```

I file esistenti vengono conservati.

## Riepilogo trace

```bash
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

## Pacchetto diagnostico

```bash
sudo ./collect-debug.sh --hours 24
```

Include trace JSONL, log testuale, journal WeeWX, journal USB kernel, `lsusb`,
stato sysfs USB, configurazione `[WMR200]`, versione e checksum del driver.
