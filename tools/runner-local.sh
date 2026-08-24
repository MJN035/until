#!/bin/sh
# 내 컴퓨터에서 Until Runner 띄우기 — 한 명령으로 끝.
#
#   tools/runner-local.sh up      # 띄우고, 격리 확인하고, 붙일 환경변수를 찍는다
#   tools/runner-local.sh down    # 내린다
#   tools/runner-local.sh status  # 지금 상태
#
# 왜 컨테이너가 둘인가 — 이게 이 스크립트의 전부다.
#
#   until-runner : `--internal` 네트워크에만 붙는다. 밖으로 **나갈 수 없다**.
#                  학생 코드가 도는 곳이라 이게 핵심이다.
#   until-relay  : 포트만 이어 주는 socat. 내부망과 바깥망에 동시에 붙는다.
#
# `--internal`은 나가는 것뿐 아니라 **들어오는 것도** 막는다(실측: `-p`를 줘도
# 호스트에서 접속 불가). 그래서 러너에 직접 포트를 열 수 없고, 아무것도 들지
# 않은 릴레이가 대신 문 앞에 선다. 릴레이는 socat 하나뿐이라 뚫려도 가져갈 게 없고,
# 러너는 여전히 인터넷이 없다.
set -eu

NET=until-int
RUNNER=until-runner-local
RELAY=until-relay
PORT="${UNTIL_RUNNER_PORT:-8901}"
IMAGE=until-runner
KEY_FILE="${UNTIL_RUNNER_KEY_FILE:-$HOME/.until-runner-key}"

here() { cd "$(dirname "$0")/.." && pwd; }

need_docker() {
    command -v docker >/dev/null 2>&1 || { echo "docker가 없습니다."; exit 1; }
    docker info >/dev/null 2>&1 || {
        echo "Docker 데몬이 꺼져 있습니다 — Docker Desktop을 먼저 켜 주세요."
        exit 1
    }
}

load_key() {
    if [ ! -f "$KEY_FILE" ]; then
        # 키는 한 번 만들어 파일로 둔다. 매번 새로 만들면 웹 쪽 설정과 어긋난다.
        (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY_FILE")
        # stdout은 **키 값 전용**이다 — 안내를 여기 섞으면 그대로 키에 붙는다(실측).
        echo "새 러너 키를 만들었습니다: $KEY_FILE" >&2
    fi
    cat "$KEY_FILE"
}

down() {
    docker rm -f "$RELAY" "$RUNNER" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "러너를 내렸습니다."
}

status() {
    if ! docker ps --format '{{.Names}}' | grep -q "^$RUNNER$"; then
        echo "러너가 떠 있지 않습니다. 'tools/runner-local.sh up'"
        return 1
    fi
    echo "러너 상태:"
    docker logs "$RUNNER" 2>&1 | head -1
    echo "헬스체크:"
    curl -s -m 5 "http://127.0.0.1:$PORT/healthz" || echo "  (응답 없음)"
    echo
}

up() {
    need_docker
    key="$(load_key)"
    cd "$(here)"

    echo "이미지를 만듭니다..."
    docker build -q -f deploy/Dockerfile.runner -t "$IMAGE" . >/dev/null

    down >/dev/null 2>&1 || true
    docker network create --internal "$NET" >/dev/null

    # 러너 — 내부망에만. 읽기 전용 루트 + 작업용 tmpfs + 자원 상한.
    docker run -d --name "$RUNNER" --network "$NET" \
        --read-only --tmpfs /tmp:rw,size=64m,exec \
        --pids-limit 128 --memory 512m --cpus 1 \
        --security-opt no-new-privileges \
        -e UNTIL_RUNNER_KEY="$key" \
        "$IMAGE" >/dev/null

    # 릴레이 — 포트만 이어 준다. 바깥망에서 시작해 내부망에 추가로 붙인다.
    docker run -d --name "$RELAY" -p "127.0.0.1:$PORT:8900" \
        alpine/socat "tcp-listen:8900,fork,reuseaddr" \
        "tcp-connect:$RUNNER:8900" >/dev/null
    docker network connect "$NET" "$RELAY"

    # 기동과 자기검증이 끝날 때까지 잠깐 기다린다.
    i=0
    while [ $i -lt 20 ]; do
        if curl -s -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
        i=$((i + 1)); sleep 1
    done

    log="$(docker logs "$RUNNER" 2>&1 | head -1)"
    echo
    echo "$log"
    case "$log" in
        *"격리 확인"*) ;;
        *)  echo
            echo "⚠ 격리를 확인하지 못했습니다 — 이 상태로는 실행 요청을 전부 거절합니다."
            echo "  (그게 맞는 동작입니다. 위 사유를 고친 뒤 다시 up 하세요.)"
            ;;
    esac
    echo
    echo "웹에 붙이려면 이 두 줄을 웹 쪽 환경에 넣으세요:"
    echo
    echo "  export UNTIL_RUNNER_URL=http://127.0.0.1:$PORT"
    echo "  export UNTIL_RUNNER_KEY=$key"
    echo
    echo "끄기: tools/runner-local.sh down"
}

case "${1:-up}" in
    up) up ;;
    down) down ;;
    status) status ;;
    *) echo "usage: $0 [up|down|status]"; exit 64 ;;
esac
