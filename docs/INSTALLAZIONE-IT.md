# Installazione WMR200 gp10

## Installazione consigliata WeeWX 5

```bash
unzip weewx-wmr200a-hardened-3.5.4-gp10-archive-clock-recovery.zip
cd weewx-wmr200a-hardened-3.5.4-gp10-archive-clock-recovery
sudo ./install.sh
```

Lo script:

1. individua `weewx.conf`;
2. crea un backup della configurazione e del driver precedente;
3. installa la regola udev;
4. prepara i log diagnostici;
5. verifica/crea la directory persistente `/var/lib/weewx` per lo stato del catch-up;
6. installa l'estensione con `weectl`;
7. configura `user.wmr200`;
8. applica i parametri gp10;
9. riavvia WeeWX salvo `--no-restart`.

## Configurazione gp10 raccomandata

```ini
[WMR200]
    model = WMR200
    driver = user.wmr200

    use_pc_time = True
    erase_archive = False
    archive_interval = 60
    archive_startup = 120
    archive_threshold = 1512000

    archive_clock_drift_max = 900
    archive_clock_wait = 180
    archive_recovery_resume = True
    archive_recovery_state_path = /var/lib/weewx/wmr200-archive-recovery.json
    archive_logger_interval = 0

    usb_read_slice_timeout = 2.0
    usb_logical_timeout_seconds = 15

    developer_trace = True
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4

    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4
```

### Significato delle nuove opzioni

- `archive_clock_drift_max = 900`: un drift iniziale superiore a 15 minuti non viene applicato ai record storici.
- `archive_clock_wait = 180`: attesa massima del clock host plausibile prima di usare l'ora nativa della console.
- `archive_recovery_resume = True`: conserva il watermark originale se WeeWX/driver si interrompe durante il catch-up.
- `archive_recovery_state_path`: file piccolo e persistente; non metterlo in `/var/log` se `/var/log` è tmpfs.
- `archive_logger_interval = 0`: rilevamento automatico della cadenza dei D2 storici. Non modifica `archive_interval = 60` usato da WeeWX in live.

## Verifica

```bash
sudo ./check-install.sh
journalctl -u weewx -n 200 --no-pager | grep -E 'wmr200|archive|clock'
```

Il driver deve mostrare `3.5.4-gp10-archive-clock-recovery`.
