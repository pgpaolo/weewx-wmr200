# Upgrade da gp9-live-scheduler a gp10-archive-clock-recovery

La gp10 parte direttamente dalla gp9. **Non modifica** decoder, checksum, stream-resync, gestione EPIPE/reopen o scheduler USB a slice da 2 secondi.

## Perché aggiornare

Il log reale del 10/08/2026 ha mostrato un Raspberry avviato senza ora NTP valida. Il primo drift host-console era circa `-48993 s`; dopo la sincronizzazione di rete il wall clock è saltato in avanti. gp9 ha chiuso il catch-up e, al riavvio successivo, ha confrontato i D2 storici con il record DB delle 11:36, marcandoli `out_of_order`.

## Nuove opzioni

Aggiungi sotto `[WMR200]`:

```ini
archive_clock_drift_max = 900
archive_clock_wait = 180
archive_recovery_resume = True
archive_recovery_state_path = /var/lib/weewx/wmr200-archive-recovery.json
archive_logger_interval = 0
```

Mantieni:

```ini
archive_interval = 60
erase_archive = False
usb_read_slice_timeout = 2.0
usb_logical_timeout_seconds = 15
```

## Dopo l'upgrade

```bash
sudo systemctl restart weewx
journalctl -u weewx -n 200 --no-pager | grep -E 'wmr200|archive|clock'
```

Durante il prossimo vero recupero storico verifica `archive_recovery_complete` e il database.
