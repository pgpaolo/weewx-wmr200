# Recupero archivio e sincronizzazione NTP su Raspberry Pi

La gp10 non richiede che NTP sia già disponibile prima dell'avvio di WeeWX. Il driver è progettato per tollerare il boot senza RTC/ora affidabile.

## Strategia

1. Il timeout del catch-up usa `time.monotonic()` e quindi non risente di correzioni NTP.
2. Se `use_pc_time = True`, il primo drift host/WMR200 viene validato.
3. Un drift enorme viene scartato come clock host non ancora plausibile.
4. Il driver continua a ricevere e accodare i D2 mentre attende un campione temporale valido.
5. Se NTP arriva, il nuovo drift viene accettato e la coda viene elaborata.
6. Se NTP non arriva entro `archive_clock_wait`, gp10 preferisce conservare i dati usando l'ora della WMR200 piuttosto che applicare un offset errato.

## Stato persistente

`/var/lib/weewx/wmr200-archive-recovery.json` contiene soltanto il watermark di recupero attivo. Viene rimosso dopo un drain completato. Serve soprattutto quando WeeWX o il driver si riavviano mentre stanno ancora scaricando una lunga coda storica.
