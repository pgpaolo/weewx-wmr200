# Test consigliato gp9

## 1. Test LIVE / heartbeat

Lasciare WeeWX attivo almeno alcune ore:

```bash
sudo ./check-install.sh
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

Controllare in particolare:

- assenza di reopen USB ripetuti;
- `usb_poll_timeout` può comparire ed è un normale timeout della finestra corta;
- `usb_read_timeout` deve comparire solo dopo almeno ~15 s di silenzio continuo;
- `heartbeat_sent.request_age_s` e `usb_control_write.lock_wait_s` devono restare
  normalmente bassi e non mostrare più ritardi dell'ordine di 15-30 secondi;
- D1 durante LIVE deve produrre `archive_ready_while_live`, non una catena DA/D2;
- la coda archive deve restare a zero durante LIVE.

## 2. Test recupero storico

Con `erase_archive = False`:

1. annotare l'ultimo timestamp presente nel database;
2. fermare WeeWX lasciando la console WMR200 alimentata;
3. attendere 30-60 minuti (o più per un test reale);
4. riavviare WeeWX;
5. attendere il completamento di `genStartupRecords()`;
6. eseguire:

```bash
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

Verificare `archive_recovery_complete`, i record ricevuti/consegnati e gli
eventuali gap. Durante questa fase D1/D2 devono essere gestiti normalmente
perché il protocol mode è `archive_recovery`.

## 3. Test ritorno a LIVE

Dopo il recupero storico verificare nel trace:

```text
protocol_mode_change -> live_pending
protocol_mode_change -> live
```

Eventuali D2 tardivi devono essere classificati come
`archive_record_dropped_while_live` e non devono accumularsi nella coda.

## 4. Raccolta diagnostica

```bash
sudo ./collect-debug.sh --hours 24
```
