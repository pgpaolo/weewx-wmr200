# Installazione WMR200 gp10

Questa guida descrive l'installazione dal repository GitHub. Il repository contiene `install.py`, quindi viene installato come estensione WeeWX.

## Requisiti

- WeeWX installato e funzionante;
- accesso amministrativo al sistema Linux per la regola udev e, nelle installazioni Debian/RPM, per i comandi `weectl`;
- WMR200/WMR200A collegata via USB;
- Python e dipendenze già richieste dalla propria installazione WeeWX.

## Installazione consigliata — WeeWX 5

Clonare il repository:

```bash
git clone https://github.com/pgpaolo/weewx-wmr200.git
cd weewx-wmr200
```

Installare l'estensione:

```bash
sudo weectl extension install .
```

Configurare WeeWX per utilizzare il driver:

```bash
sudo weectl station reconfigure --driver=user.wmr200
```

> Nelle installazioni WeeWX possedute direttamente dall'utente corrente, `sudo` potrebbe non essere necessario per `weectl`.

## Regola udev USB

Il file `install.py` installa il driver WeeWX, ma non copia automaticamente la regola udev di sistema. Installarla una volta con:

```bash
sudo install -m 0644 util/udev/rules.d/wmr200.rules /etc/udev/rules.d/wmr200.rules
sudo udevadm control --reload-rules
```

Dopo il reload, scollegare e ricollegare il cavo USB della WMR200 oppure riavviare il Raspberry Pi.

La regola assegna il dispositivo USB Oregon Scientific `0fde:ca01` al gruppo `weewx`, con permessi `0660`, e disabilita l'autosuspend USB per il dispositivo.

## Directory persistente per il recupero archivio

Con:

```ini
archive_recovery_resume = True
archive_recovery_state_path = /var/lib/weewx/wmr200-archive-recovery.json
```

assicurarsi che `/var/lib/weewx` sia persistente e scrivibile dal servizio WeeWX:

```bash
sudo install -d -o weewx -g weewx -m 0755 /var/lib/weewx
```

Non collocare lo state file sotto `/var/log` se `/var/log` è montato in `tmpfs`.

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
    ignore_checksum = False
    sensor_status = True

    archive_clock_drift_max = 900
    archive_clock_wait = 180
    archive_recovery_resume = True
    archive_recovery_state_path = /var/lib/weewx/wmr200-archive-recovery.json
    archive_logger_interval = 0

    usb_write_retries = 3
    usb_read_retries = 2
    usb_retry_delay = 0.5
    usb_reopen_on_failure = True
    usb_read_slice_timeout = 2.0
    usb_logical_timeout_seconds = 15
    usb_timeout_warn_consecutive = 2
    usb_timeout_error_consecutive = 4
    usb_health_interval = 300

    developer_trace = True
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = True
    developer_trace_include_packets = True

    driver_file_log = True
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4

    [[sensor_map]]
```

### Significato delle opzioni gp10

- `archive_clock_drift_max = 900`: un drift iniziale superiore a 15 minuti non viene applicato automaticamente ai record storici.
- `archive_clock_wait = 180`: attesa massima del clock host plausibile prima del fallback all'ora nativa della console.
- `archive_recovery_resume = True`: conserva il watermark originale se WeeWX/driver si interrompe durante il catch-up.
- `archive_recovery_state_path`: state file persistente del recupero.
- `archive_logger_interval = 0`: rilevamento automatico della cadenza D2 storica; non modifica `archive_interval = 60` usato da WeeWX in live.

## Riavvio e verifica

Riavviare WeeWX:

```bash
sudo systemctl restart weewx
```

Verificare versione e messaggi principali:

```bash
journalctl -u weewx -n 250 --no-pager | grep -Ei 'wmr200|archive|clock|usb'
```

Il log deve riportare:

```text
driver version is 3.5.4-gp10-archive-clock-recovery
```

Per verificare la sincronizzazione NTP del Raspberry Pi:

```bash
timedatectl
```

Durante un test di recupero archivio sono particolarmente utili gli eventi:

```text
host_clock_not_ready
host_clock_ready
archive_recovery_start
archive_record_evaluated
archive_recovery_complete
```

## Aggiornamento da gp9

Vedere [UPGRADE-GP9-TO-GP10-IT.md](UPGRADE-GP9-TO-GP10-IT.md).
