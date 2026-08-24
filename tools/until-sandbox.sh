#!/bin/sh
# Until Local Agent 샌드박스 래퍼 — 작업공간만 쓰기 가능 + 네트워크 차단.
#
#   사용: until-sandbox.sh [--allow-write 경로]... <작업공간> <명령> [인자...]
#   설정: UNTIL_AGENT_SANDBOX="/절대경로/until-sandbox.sh,{workspace}"
#
# `--allow-write`는 공식 CLI가 실행 중 자기 설정·토큰을 갱신해야 할 때를 위한
# **좁은 예외**다. 환경변수가 아니라 **인자**인 이유: 커널이 에이전트에게 넘기는
# 환경을 세탁하므로(`sanitize_environment`) 환경변수로는 이 래퍼까지 도달하지
# 않는다. 운영자가 UNTIL_AGENT_SANDBOX에 직접 적어야 한다:
#   UNTIL_AGENT_SANDBOX="/…/until-sandbox.sh,--allow-write,/home/u/.config/그CLI,{workspace}"
# 연 만큼 격리가 약해진다 — 열었으면 `--verify-sandbox`를 다시 돌리고 무엇을 왜
# 열었는지 적어 둘 것.
#
# 왜 이게 있나: Windows 네이티브에는 이 계약을 만족하는 기본 샌드박스가 없고,
# bubblewrap·firejail은 별도 설치가 필요하다. 이 스크립트는 **추가 설치 없이**
# util-linux의 `unshare`만으로 같은 두 가지를 만든다 — WSL2 Ubuntu 24.04에서
# `--verify-sandbox`로 세 항목 모두 확인했다(2026-08-21).
#
# 하는 일
#   --user --map-root-user : 권한 없는 사용자도 네임스페이스 안에서 마운트 가능
#   --mount + remount ro / : 루트 전체를 읽기 전용으로
#   --bind <작업공간> rw    : 작업공간만 다시 쓰기 가능으로
#   --net                  : 네트워크 네임스페이스 분리(인터페이스 없음)
#
# 한계 — 신고하기 전에 반드시 `python -m until.runtime --verify-sandbox`로 확인할 것.
#   * 커널이 권한 없는 user namespace를 막아 두면(일부 배포판·정책) 설정이 실패한다.
#     그때는 SANDBOX_SETUP_FAILED를 내고 종료하므로 조용히 뚫린 채 돌지는 않는다.
#   * 공식 CLI의 로그인 상태가 홈 디렉터리에 있으면 읽기는 되지만 쓰기는 막힌다.
#     CLI가 실행 중 토큰 갱신을 위해 쓰기를 요구하면 그 경로를 따로 열어 줘야 하고,
#     연 만큼 격리가 약해진다 — 열었다면 다시 검증하고 무엇을 열었는지 적어 둘 것.
#   * 이 스크립트는 격리를 **만들 뿐** 신고하지 않는다. 신고는
#     UNTIL_AGENT_SANDBOX_ISOLATES 이고, 검증을 통과한 뒤에만 적는다.
set -eu

ALLOW=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --allow-write)
            [ "$#" -ge 2 ] || { echo "--allow-write needs a path" >&2; exit 64; }
            ALLOW="${ALLOW}${ALLOW:+:}$2"
            shift 2
            ;;
        *) break ;;
    esac
done

if [ "$#" -lt 2 ]; then
    echo "usage: $0 [--allow-write PATH]... <workspace> <command> [args...]" >&2
    exit 64
fi

WORKSPACE="$1"
shift

if [ ! -d "$WORKSPACE" ]; then
    echo "workspace is not a directory: $WORKSPACE" >&2
    exit 65
fi

exec unshare --user --map-root-user --mount --net --fork sh -c '
    # sh -c "스크립트" arg0 arg1 arg2... 에서 $0=arg0(작업공간), $1=arg1(예외 경로),
    # "$@"=arg2..(명령)이다. 아래 shift 하나는 **예외 경로를 걷어내는 것**이고,
    # 명령의 첫 단어를 먹지 않는다. 예전에 shift가 하나 더 있어 python3이 날아가고
    # 그 다음 인자를 실행하려 들었다("exec: ...t.py: Permission denied").
    WS="$0"
    ALLOW="$1"
    shift
    # 마운트 전파를 끊는다 — 안 끊으면 여기서 한 remount가 호스트로 새어 나갈 수 있다.
    mount --make-rprivate / 2>/dev/null
    mount -o remount,ro,bind / 2>/dev/null || {
        echo "SANDBOX_SETUP_FAILED: cannot remount / read-only" >&2
        exit 90
    }
    mount --bind "$WS" "$WS" || exit 91
    mount -o remount,rw,bind "$WS" || exit 92
    OLDIFS=$IFS; IFS=:
    for extra in $ALLOW; do
        [ -n "$extra" ] && [ -e "$extra" ] || continue
        mount --bind "$extra" "$extra" || exit 94
        mount -o remount,rw,bind "$extra" || exit 95
    done
    IFS=$OLDIFS
    cd "$WS" || exit 93
    exec "$@"
' "$WORKSPACE" "$ALLOW" "$@"
