# Installazione WMR200 3.5.4-gp8-archive-trace

## Aggiornamento da gp7 o da un driver WMR200 già operativo

Prima di modificare il driver:

```bash
sudo systemctl stop weewx
```

Individuare il `wmr200.py` attualmente utilizzato da WeeWX e crearne una copia di sicurezza. Nelle installazioni package di WeeWX 5 il driver utente è normalmente sotto `/etc/weewx/bin/user/`.

Esempio:

```bash
sudo cp /etc/weewx/bin/user/wmr200.py \
  /etc/weewx/bin/user/wmr200.py.gp7-backup
sudo cp bin/user/wmr200.py /etc/weewx/bin/user/wmr200.py
```

Preparare il percorso dei log:

```bash
sudo mkdir -p /var/log/weewx
sudo chown weewx:weewx /var/log/weewx
sudo chmod 0755 /var/log/weewx
```

Se `/var/log` è in tmpfs, assicurarsi che `/var/log/weewx` venga ricreata ad ogni boot con proprietario `weewx`.

## Regola udev

Il repository contiene:

```text
util/udev/rules.d/wmr200.rules
```

Installarla con:

```bash
sudo cp util/udev/rules.d/wmr200.rules \
  /etc/udev/rules.d/99-wmr200.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

La regola assegna il dispositivo al gruppo `weewx` con mode `0660` e disabilita l'autosuspend USB per la WMR200.

Dopo la modifica è preferibile scollegare/ricollegare il cavo USB o riavviare il sistema.

## Configurazione

Usare la sezione `[WMR200]` riportata nel README. I parametri diagnostici consigliati sono:

```ini
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

Lasciare:

```ini
erase_archive = False
```

per preservare l'archivio interno della console.

## Avvio

```bash
sudo systemctl start weewx
sudo systemctl status weewx --no-pager
```

Controllare:

```bash
sudo journalctl -u weewx -n 100 --no-pager
sudo tail -f /var/log/weewx/wmr200-debug.log
```

## Installazione tramite ExtensionInstaller

Dalla root del repository è inoltre possibile usare:

```bash
sudo weectl extension install .
sudo weectl station reconfigure --driver=user.wmr200
```

La regola udev resta un componente di sistema e deve essere installata separatamente come indicato sopra.
