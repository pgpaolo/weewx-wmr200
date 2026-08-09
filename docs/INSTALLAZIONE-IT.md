# Installazione - WMR200 3.5.4-gp9-live-scheduler

## Installazione automatica WeeWX 5

```bash
unzip weewx-wmr200a-hardened-3.5.4-gp9-live-scheduler.zip
cd weewx-wmr200a-hardened-3.5.4-gp9-live-scheduler
sudo ./install.sh
```

L'installer:

1. individua `weewx.conf` oppure accetta `--config PATH`;
2. crea un backup in `/var/backups/weewx-wmr200/<timestamp>/`;
3. installa la regola udev `99-wmr200.rules`;
4. disabilita l'autosuspend USB per `0fde:ca01`;
5. prepara `/var/log/weewx` e i due file diagnostici;
6. crea `/etc/tmpfiles.d/weewx-wmr200.conf` se disponibile;
7. installa l'estensione tramite `weectl extension install`;
8. configura `driver = user.wmr200`;
9. applica i parametri diagnostici e scheduler gp9;
10. riavvia WeeWX salvo `--no-restart`.

## Configurazione consigliata

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

    # Trace strutturato
    developer_trace = True
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = True
    developer_trace_include_packets = True

    # Log testuale asincrono
    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4
```

Il timeout `usb_read_slice_timeout = 2.0` serve solo a rilasciare frequentemente
il lock PyUSB. Il driver considera un vero timeout di comunicazione solo quando
il silenzio continuo supera `usb_logical_timeout_seconds = 15`.

Ogni famiglia di log conserva il file attivo + 4 backup: massimo 5 file e circa
50 MB per famiglia con i valori sopra.

## Verifica

```bash
sudo ./check-install.sh
sudo systemctl status weewx --no-pager
sudo tail -f /var/log/weewx/wmr200-debug.log
```

Per verificare la latenza D0:

```bash
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

## Backup / rollback

Il backup pre-installazione viene indicato a video. `uninstall.sh` rimuove
l'estensione e la regola di supporto; i log raccolti non vengono cancellati.
