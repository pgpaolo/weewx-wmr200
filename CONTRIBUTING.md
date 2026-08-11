# Contributing

Thank you for helping test and improve the WeeWX WMR200 hardened driver.

## Before opening an issue

Please check that you are using the latest published driver revision and search existing issues for the same symptom.

For operational problems, collect the smallest useful diagnostic window around the incident. Do not post passwords, API keys, private URLs or unrelated configuration sections.

## Bug reports

A useful bug report should include:

- driver version;
- WeeWX version;
- operating system and architecture;
- Raspberry Pi / host model;
- WMR200 or WMR200A console model;
- relevant `[WMR200]` configuration with sensitive values removed;
- whether the issue concerns LIVE data, USB communication, archive recovery, NTP/clock handling or decoding;
- exact local date/time of the incident;
- relevant developer trace / driver log or diagnostic bundle.

For archive recovery problems, also include:

- last valid WeeWX database timestamp before the gap;
- first valid timestamp after the gap;
- whether the console remained powered while WeeWX was stopped;
- whether the Raspberry Pi booted before network/NTP synchronization;
- `timedatectl` output after the system clock is synchronized.

## Pull requests

Keep changes focused. Avoid combining protocol changes, formatting changes and unrelated refactoring in one pull request.

Before submitting:

```bash
python3 -m py_compile bin/user/wmr200.py
python3 -m py_compile install.py
```

When changing protocol, USB or archive behavior, describe the hardware evidence or trace that motivated the change and the regression test used to validate it.

## Driver stability principle

The gp-series intentionally favors field-proven stability. Decoder, checksum, EPIPE/reopen, stream-resync and USB scheduling behavior should not be changed without a reproducible problem or trace evidence.
