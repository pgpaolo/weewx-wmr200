# Upgrade da 3.5.4-gp7-streamresync a gp8

La base USB/protocollo gp7 è mantenuta. gp8 aggiunge diagnostica archivio e il
vero `driver_file_log` asincrono.

Per sostituire solo il driver:

```bash
sudo systemctl stop weewx
sudo cp /percorso/attuale/bin/user/wmr200.py /var/backups/wmr200.py.gp7
sudo cp bin/user/wmr200.py /percorso/weewx/bin/user/wmr200.py
sudo systemctl start weewx
```

È preferibile usare `install.sh`, perché prepara anche udev, permessi log e
`systemd-tmpfiles`.

Configurare entrambi i log a 10 MB + 4 backup. `erase_archive` deve restare
`False` per i test di sincronizzazione storica.
