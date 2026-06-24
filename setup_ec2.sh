#!/bin/bash
# EC2 최초 1회 실행 — 의존성 설치 + cron 등록
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_HOUR=1   # UTC 01:00 = KST 10:00

echo "=== [1/4] 시스템 패키지 업데이트 ==="
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv fonts-nanum git

echo "=== [2/4] Python 의존성 설치 ==="
pip3 install --quiet --break-system-packages requests pandas matplotlib mplfinance koreanize-matplotlib

echo "=== [3/4] .env 파일 확인 ==="
if [ ! -f "$REPO_DIR/.env" ]; then
    echo "⚠️  .env 파일이 없습니다. .env.example을 복사해서 값을 채워주세요:"
    echo "    cp $REPO_DIR/.env.example $REPO_DIR/.env"
    echo "    nano $REPO_DIR/.env"
fi

echo "=== [4/4] cron 등록 (매일 KST 10:00 = UTC 01:00) ==="
CRON_JOB="0 ${CRON_HOUR} * * 1-5 $REPO_DIR/run_report.sh >> $REPO_DIR/logs/cron.log 2>&1"
# 중복 방지: 기존 등록 제거 후 재등록
(crontab -l 2>/dev/null | grep -v "run_report.sh"; echo "$CRON_JOB") | crontab -
mkdir -p "$REPO_DIR/logs"

echo ""
echo "✅ 설치 완료!"
echo ""
echo "다음 단계:"
echo "  1. cp $REPO_DIR/.env.example $REPO_DIR/.env"
echo "  2. nano $REPO_DIR/.env  (API 키 / Gmail 정보 입력)"
echo "  3. bash $REPO_DIR/run_report.sh  (테스트 실행)"
