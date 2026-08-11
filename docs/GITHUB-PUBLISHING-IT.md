# Aggiornamento metadata GitHub — gp10

Questo pacchetto modifica esclusivamente documentazione e metadata GitHub. Non contiene e non sostituisce `bin/user/wmr200.py` né `install.py`.

## File da sostituire

Sostituire nel repository:

- `README.md`
- `docs/INSTALLAZIONE-IT.md`

Le sostituzioni correggono i riferimenti a `install.sh` e `check-install.sh`, che non sono presenti nella root del repository pubblico, e documentano l'installazione come estensione WeeWX tramite `weectl extension install`.

## File nuovi da aggiungere

Aggiungere:

```text
.gitignore
CONTRIBUTING.md
SECURITY.md
.github/
  ISSUE_TEMPLATE/
    bug_report.yml
    hardware_test.yml
    config.yml
  pull_request_template.md
  workflows/
    validate.yml
```

## Procedura con l'interfaccia web GitHub

1. Aprire il repository `pgpaolo/weewx-wmr200` sul branch `main`.
2. Selezionare **Add file → Upload files**.
3. Estrarre lo ZIP di questo pacchetto sul PC.
4. Trascinare nella pagina GitHub **il contenuto della cartella estratta**, inclusa la cartella `.github`.
5. GitHub mostrerà `README.md` e `docs/INSTALLAZIONE-IT.md` come file modificati e gli altri come nuovi file.
6. Verificare che `bin/user/wmr200.py` e `install.py` non risultino modificati da questo upload.
7. Usare come commit message, ad esempio:

```text
Improve GitHub documentation, issue templates and CI
```

8. Eseguire il commit direttamente su `main` oppure, se si preferisce, creare un branch e una Pull Request.
9. Aprire la scheda **Actions**: il workflow `Validate` deve compilare correttamente `wmr200.py` e `install.py` e verificare la coerenza della stringa di versione gp10.

## Topics consigliati

Dalla sezione **About → Settings** del repository aggiungere:

```text
weewx
weewx-driver
wmr200
wmr200a
oregon-scientific
weather-station
raspberry-pi
usb
python
```

## Descrizione About consigliata

```text
Hardened WeeWX driver for Oregon Scientific WMR200/WMR200A with USB recovery, diagnostics and archive catch-up.
```

## Release gp10

Dopo il test hardware definitivo, creare il tag:

```text
v3.5.4-gp10-archive-clock-recovery
```

Titolo release consigliato:

```text
WMR200 3.5.4-gp10 — Archive Clock Recovery
```

Finché il test boot senza ora valida → NTP → recupero archivio non è concluso, la release può essere marcata **Pre-release**.

## Licenza

Questo pacchetto non aggiunge un file `LICENSE`. Il repository upstream pubblico `weewx/weewx-wmr200` mostra l'attribuzione originale ma, allo stato verificato, non espone un file di licenza nella root. È preferibile non assegnare arbitrariamente MIT/GPL/BSD senza aver prima confermato il testo di licenza applicabile alla sorgente originale.
