# Release notes — 3.5.4-gp10-archive-clock-recovery

## Summary

gp10 fixes a real startup catch-up failure observed on a Raspberry Pi where WeeWX started before NTP had corrected the host clock. The WMR200 logger contained the missing records and sent D2 history correctly, but gp9 could terminate archive recovery after a wall-clock jump and later classify old history against the current database tail.

## Behavioural changes

1. **Monotonic recovery timer** — NTP, DST or manual wall-clock steps do not alter the 120-second archive quiet timer.
2. **Boot-clock gate** — a first drift sample larger than `archive_clock_drift_max` is rejected and re-sampled.
3. **Console-time fallback** — after `archive_clock_wait`, historical data is preserved with zero host adjustment if NTP is still unavailable.
4. **Correct ordering** — D2 ordering is checked against the previous D2 record, not `since_ts`.
5. **Interrupted recovery resume** — gp10 can keep the original catch-up origin in `/var/lib/weewx/wmr200-archive-recovery.json` until a clean drain completes.
6. **D2 cadence auto-detection** — historical logger spacing can be learned independently of the normal WeeWX live archive interval.

## Recommended validation

Keep `erase_archive = False`. Stop WeeWX for at least 30–60 minutes while leaving the WMR200 powered, then boot/restart the Raspberry Pi with networking initially unavailable or delayed. After NTP synchronization, verify multiple `archive_record_evaluated` events with `disposition=yielded` and confirm the missing period appears in the WeeWX database.

## Upgrade compatibility

Existing gp9 configuration remains valid. The new gp10 options have safe defaults and can be omitted, but adding them explicitly is recommended for reproducible diagnostics.
