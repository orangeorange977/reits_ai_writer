#!/bin/sh
# REIT-AI 申报系统每日数据备份（步骤 3.7）
# 打包 data/workspace（数据库 + 项目数据 + 生成产物），保留最近 7 天。
# 安装：crontab -e 添加  0 3 * * * /home/ubuntu/reit-app/deploy/backup.sh
BACKUP_DIR=/home/ubuntu/reit-backups
DATA_DIR=/home/ubuntu/reit-app/data
mkdir -p "$BACKUP_DIR"
ts=$(date +%Y%m%d)
tar -czf "$BACKUP_DIR/reit-$ts.tar.gz" -C "$DATA_DIR" workspace
find "$BACKUP_DIR" -name 'reit-*.tar.gz' -mtime +7 -delete
echo "[$(date)] 备份完成: $BACKUP_DIR/reit-$ts.tar.gz"
