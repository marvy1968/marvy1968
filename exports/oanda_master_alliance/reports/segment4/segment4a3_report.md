# OANDA Master AI — Segment 4A-3

## Deployment

- Generated: **2026-10-02T04:16:28.901811-04:00**
- Deployment mode: **research_services**
- Installer completed: **True**
- Systemd units copied: **True**
- Services automatically enabled: **False**

## Safety

- Safety passed: **True**
- Practice only: **True**
- Live environment allowed: **False**
- Automatic execution: **False**
- Practice-order submission: **False**

## Services and timers

| Unit | Active | Enabled |
|---|---|---|
| oanda-master-ai.service | inactive | disabled |
| oanda-master-ai-watchdog.service | inactive | disabled |
| oanda-master-ai-health.timer | inactive | Failed to get unit file state for oanda-master-ai-health.timer: No such file or directory |
| oanda-master-ai-maintenance.timer | active | enabled |
| oanda-master-ai-backup.timer | inactive | Failed to get unit file state for oanda-master-ai-backup.timer: No such file or directory |
| oanda-master-ai-report.timer | active | enabled |

## Latest backup

- Successful: **False**
- Archive: **/home/moonrider1968/oanda_master_alliance_backups/oanda_master_alliance_20261001_034535.tar.gz**
- Size: **0 bytes**
- Error: **PermissionError: [Errno 13] Permission denied: '/home/moonrider1968/oanda_master_alliance/logs/segment4_backup_systemd.log'**

## Latest maintenance

- Successful: **False**
- Actions: **6**
- Free disk: **33248.03 MB**

## Latest alert

- Event: **maintenance_failure**
- Severity: **error**
- Delivered: **True**
- Suppressed: **False**

## Operational guardrails

- Systemd installation does not enable the daemon automatically.
- Automatic restart inside the watchdog remains disabled.
- The main systemd service uses on-failure restart with a burst limit.
- Recovery validation can block unsafe runtime startup.
- Credentials are excluded from application backups.
- Backups include checksums and archive validation.
- Alerts do not authorize or submit trades.
- Automatic execution and order submission remain disabled.
