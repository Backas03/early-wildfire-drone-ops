#!/usr/bin/env bash
# 엣지 AI를 라즈베리파이가 부팅할 때 자동으로 켜지게 한다 (sudo 필요 없음).
#
#   bash edge-ai/deploy/install.sh
#
# systemd 사용자 서비스라 사용자가 로그인할 때 켜진다. 바탕화면 자동 로그인
# (raspi-config → System Options → Boot / Auto Login → Desktop Autologin)이면 부팅과 함께 켜진다.
# 로그인 없이 켜려면 한 번: sudo loginctl enable-linger "$USER"
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
unit_dir="$HOME/.config/systemd/user"
env_file="$HOME/.config/edge-ai.env"

mkdir -p "$unit_dir"
sed "s#@REPO@#$repo#g" "$here/edge-ai.service" > "$unit_dir/edge-ai.service"
if [ ! -f "$env_file" ]; then
  sed "s#@HOME@#$HOME#g" "$here/edge-ai.env.example" > "$env_file"
  echo "설정 파일을 만들었다: $env_file  (EDGE_ARGS 확인)"
fi
systemctl --user daemon-reload
systemctl --user enable edge-ai.service

cat <<EOF
설치 끝 (부팅할 때 자동으로 켜짐).
  지금 켜기·다시 켜기: systemctl --user restart edge-ai
  끄기:               systemctl --user stop edge-ai
  자동 실행 해제:      systemctl --user disable edge-ai
  상태:               systemctl --user status edge-ai
  로그:               tail -f ~/edge-ai.log
EOF
