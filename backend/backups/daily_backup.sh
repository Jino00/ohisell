#!/bin/bash
# ohisell DB 일일 백업 (.backup, 14일 보관)
set -e
D=/home/ubuntu/ohisell/backend
TS=$(date +%Y%m%d_%H%M%S)
sqlite3 "$D/ohisell.db" ".backup $D/backups/ohisell_daily_${TS}.db"
find "$D/backups" -name "ohisell_daily_*.db" -mtime +14 -delete
