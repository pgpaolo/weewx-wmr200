# Upgrade da gp8-archive-trace a gp9-live-scheduler

La gp9 parte dalla gp8 e non modifica decoder, checksum, mapping sensori,
stream-resync o logica di reopen USB.

## Cambiamenti funzionali

1. `interruptRead()` usa finestre brevi da 2 s anziché trattenere il lock fino a
   15 s.
2. I timeout di salute restano logici a 15 s di silenzio continuo.
3. D1/D2 sono state rese dipendenti dallo stato `archive_recovery`/`live`.
4. I D2 tardivi in LIVE non vengono più accumulati in `PacketArchive.pkt_queue`.
5. Il trace misura la latenza reale degli heartbeat.

## Config da aggiungere

```ini
usb_read_slice_timeout = 2.0
usb_logical_timeout_seconds = 15
```

Gli altri parametri gp8 possono rimanere invariati.

## Upgrade rapido

```bash
sudo systemctl stop weewx
sudo ./install.sh --no-restart
sudo systemctl start weewx
```

Dopo il riavvio verificare con `tools/trace-summary.py` che i heartbeat non
abbiano più latenze anomale e che gli archive packet non si accumulino in LIVE.
