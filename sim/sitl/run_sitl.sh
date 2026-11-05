#!/usr/bin/env bash
# ArduCopter SITL(가상 드론)을 WSL(Ubuntu)에서 실행한다.
#
#   wsl bash sim/sitl/run_sitl.sh
#
# 처음 실행할 때 ArduPilot 공식 서버에서 SITL 실행 파일과 기본 파라미터를 받는다.
# 실행 파일과 SITL이 만드는 파일(eeprom.bin, 로그)은 WSL 홈의 ~/fire-drone-sitl 에 둔다.
# 윈도우 폴더(/mnt/c/...)에서 실행하면 SITL이 시작 직후 꺼진다 (eeprom.bin을 못 다룸).
#
# 접속 포트 (윈도우에서 127.0.0.1로 접속 가능):
#   5760  drone-agent 용  ← SITL은 여기에 누가 접속할 때까지 시작을 기다린다
#   5762  Mission Planner 용 (TCP 연결)
#   5763  sim/sitl/pilot.py (가상 조종사: 시동·이륙) 용
#
# 환경 변수:
#   SITL_HOME="위도,경도,해발고도,방향"   홈 위치 (기본: 대구대 경산캠퍼스 근처)
#   SITL_SPEEDUP=2                         시뮬레이션 배속 (기본 1)
#   SITL_WIPE=1                            파라미터·미션 초기화 (eeprom.bin 지움)
set -euo pipefail

REPO_SITL="$(cd "$(dirname "$0")" && pwd)"
WORK="${SITL_DIR:-$HOME/fire-drone-sitl}"
mkdir -p "$WORK"
cd "$WORK"

if [ ! -x arducopter ]; then
  if [ -f "$REPO_SITL/bin/arducopter" ]; then
    cp "$REPO_SITL/bin/arducopter" .   # 예전 스크립트가 받아 둔 파일
  else
    echo "SITL 실행 파일 다운로드 (firmware.ardupilot.org)..."
    curl -fL -o arducopter.part https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter
    mv arducopter.part arducopter
  fi
  chmod +x arducopter
fi
if [ ! -f copter.parm ]; then
  echo "기본 파라미터 다운로드 (github.com/ArduPilot)..."
  curl -fL -o copter.parm https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm
fi

if [ "${SITL_WIPE:-0}" = "1" ]; then
  echo "eeprom.bin 삭제 (파라미터·미션 초기화)"
  rm -f eeprom.bin
fi

# 기본 홈: 대구대 경산캠퍼스 근처 (대략적인 좌표)
HOME_LOC="${SITL_HOME:-35.8964,128.8495,100,0}"
echo "SITL 시작: 홈=$HOME_LOC, 배속=${SITL_SPEEDUP:-1}, 작업 폴더=$WORK"
echo "drone-agent가 5760 포트에 접속하면 시작한다."
# exec로 실행하지 않는다: 그러면 SITL의 부모가 WSL 초기 프로세스가 되고, SITL은 이를 "부모가 죽었다"로
# 보고 시작 직후 스스로 꺼진다. stdbuf는 출력이 파이프로 갈 때도 줄 단위로 바로 보이게 한다.
stdbuf -oL -eL ./arducopter --model quad --speedup "${SITL_SPEEDUP:-1}" --defaults copter.parm --home "$HOME_LOC" -I0
