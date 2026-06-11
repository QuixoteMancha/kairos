# Kairos systemd units

## Backup automático cada 6h

```bash
# Instalar (como root o con sudo)
sudo cp scripts/systemd/kairos-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kairos-backup.timer

# Verificar
systemctl list-timers kairos-backup.timer
journalctl -u kairos-backup.service -n 50
```

Los backups quedan en `data/backups/kairos_YYYYMMDD_HHMMSS.db.gz`
con sidecar `.sha256` y rotación a 30 archivos (`--keep 30`).

## Restore

```bash
python scripts/restore_db.py data/backups/kairos_20260516_010000.db.gz
```

Verifica SHA-256 antes de sobreescribir y deja una safety copy
`kairos.db.pre_restore`.
