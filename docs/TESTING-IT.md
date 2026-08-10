# Test consigliato gp10

## 1. Test automatici

```bash
python3 -m py_compile bin/user/wmr200.py
for t in tests/test_*.py; do python3 "$t" || exit 1; done
```

I test gp10 includono:

- rigetto del drift reale anomalo `-48993 s`;
- successiva accettazione di un drift plausibile `13 s` dopo NTP;
- separazione `since_ts` / sequenza D2;
- fallback a timestamp console;
- resume da watermark persistente;
- regressioni USB gp9, checksum, EPIPE/reopen, stream resync e rotazione log.

## 2. Test hardware NTP

1. `sudo systemctl stop weewx`.
2. Lascia la WMR200 accesa almeno 30–60 minuti.
3. Riavvia il Raspberry con rete inizialmente non disponibile oppure con NTP ritardato.
4. Fai arrivare la rete e lascia sincronizzare NTP.
5. Attendi la fine del catch-up.
6. Controlla:

```bash
grep -E 'host_clock_|archive_clock_|archive_recovery|archive_record_evaluated' /var/log/weewx/wmr200-developer-trace.jsonl
```

7. Verifica SQLite sul periodo di fermo.

## 3. Criteri di successo

- nessuna chiusura catch-up causata da salto dell'orologio;
- nessun `out_of_order` solo perché il D2 è precedente a `since_ts`;
- record storici `yielded` presenti nel DB;
- state file cancellato a drain completato;
- gp9 scheduler ancora stabile: niente regressioni di heartbeat/USB.
