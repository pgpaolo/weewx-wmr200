# Test consigliato gp8

## 1. Test live

Lasciare WeeWX attivo e verificare per alcune ore:

```bash
sudo ./check-install.sh
sudo tail -f /var/log/weewx/wmr200-debug.log
```

Controllare che non compaiano riavvii ripetuti, reopen continui o code di trace
sature.

## 2. Test recupero storico

Con `erase_archive = False`:

1. annotare l'ultimo timestamp presente nel database;
2. fermare WeeWX lasciando la console WMR200 alimentata;
3. attendere un periodo significativo (per esempio 30-60 minuti per il test);
4. riavviare WeeWX;
5. attendere il completamento di `genStartupRecords`;
6. eseguire:

```bash
sudo ./tools/trace-summary.py /var/log/weewx/wmr200-developer-trace.jsonl\*
```

Nel riepilogo `Last archive recovery` verificare:

- `outcome: archive_drained`;
- `received/yielded` plausibili;
- `gaps: 0` se la memoria console contiene tutti gli intervalli;
- assenza di `threshold` o `subminute` drops inattesi.

## 3. Raccolta diagnostica

```bash
sudo ./collect-debug.sh --hours 24
```

Conservare il `.tar.gz` generato per l'analisi.
