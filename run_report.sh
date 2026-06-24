#!/bin/bash
# cron이 매일 호출하는 실행 스크립트
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# .env 로드
if [ -f "$REPO_DIR/.env" ]; then
    set -a
    source "$REPO_DIR/.env"
    set +a
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 리포트 시작"
python3 "$REPO_DIR/fetch_and_report.py"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 리포트 완료"
