"""설정한 샌드박스가 **실제로** 막는지 직접 시험한다.

`UNTIL_AGENT_SANDBOX_ISOLATES`는 기능을 켜는 스위치가 아니라 "이 래퍼가 이미
막고 있다"는 **신고**다. 신고가 틀리면 커널은 거짓 신뢰를 근거로 프로세스를
띄운다 — 문서가 여러 번 경고하지만, 확인 방법이 "직접 해 보세요"뿐이라
현실적으로 아무도 확인하지 않는다. 그래서 확인을 코드로 만든다.

세 가지를 실제로 시도해 보고 **실패해야 통과**로 친다.
  1. `network`    — 샌드박스 안에서 밖으로 나가는 연결이 실패하는가
  2. `filesystem` — 작업공간 **밖** 경로에 쓰기가 실패하는가
  3. (부수) 작업공간 **안** 쓰기는 성공하는가 — 이게 막히면 에이전트가 일을 못 한다

시험은 샌드박스가 감싸는 명령으로 파이썬 한 줄을 돌린다. 파이썬이 샌드박스
안에 없으면 시험 자체를 할 수 없고, 그때는 **모른다**로 보고한다 — 모르는 것을
통과로 적지 않는 것이 이 모듈의 전부다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: 샌드박스 안에서 돌릴 시험들. 각 코드는 "막혔으면 BLOCKED, 뚫렸으면 OPEN"을 찍는다.
#: 표준 라이브러리만 쓰고 1초 안에 끝난다.
_NETWORK_PROBE = (
    "import socket,sys\n"
    "socket.setdefaulttimeout(3)\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1',53),3).close()\n"
    "    print('OPEN')\n"
    "except Exception:\n"
    "    print('BLOCKED')\n"
)
_ESCAPE_PROBE = (
    "import sys,os\n"
    "target=sys.argv[1]\n"
    "try:\n"
    "    open(target,'wb').write(b'x')\n"
    "    os.unlink(target)\n"
    "    print('OPEN')\n"
    "except Exception:\n"
    "    print('BLOCKED')\n"
)
_INSIDE_PROBE = (
    "import sys,os\n"
    "target=sys.argv[1]\n"
    "try:\n"
    "    open(target,'wb').write(b'x')\n"
    "    os.unlink(target)\n"
    "    print('OPEN')\n"
    "except Exception:\n"
    "    print('BLOCKED')\n"
)

#: 시험용 최소 환경. **비우면 안 된다** — 샌드박스 래퍼 자신이 `unshare` 같은
#: 도구를 PATH로 찾는데, 빈 환경에서는 그걸 못 찾아 시험 전체가 '모름'이 된다.
#: 시크릿은 어차피 한 줄도 넣지 않으므로 여기 있는 것이 전부다.
_PROBE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
              "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


class _NoSandbox:
    """대조군용 — 아무것도 감싸지 않고 그대로 실행한다."""

    @staticmethod
    def wrap(argv, workspace):
        return tuple(argv)


_NO_SANDBOX = _NoSandbox()


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str          # pass | fail | unknown
    detail: str = ""


@dataclass(frozen=True)
class SandboxReport:
    results: tuple[ProbeResult, ...]
    claimed_filesystem: bool = False
    claimed_network: bool = False

    def status_of(self, name: str) -> str:
        for item in self.results:
            if item.name == name:
                return item.status
        return UNKNOWN

    @property
    def safe_to_claim(self) -> tuple[str, ...]:
        """시험으로 **증명된** 격리만 — 이것만 신고해도 된다."""
        return tuple(name for name in ("filesystem", "network")
                     if self.status_of(name) == PASS)

    @property
    def overclaimed(self) -> tuple[str, ...]:
        """신고했는데 시험이 증명하지 못한 것 — 가장 위험한 상태."""
        claimed = {"filesystem": self.claimed_filesystem,
                   "network": self.claimed_network}
        return tuple(name for name, on in claimed.items()
                     if on and self.status_of(name) != PASS)


def verify(sandbox, workspace: Path, *, python: str = "python",
           runner=None, timeout: int = 30,
           escape_targets: "tuple[str, ...] | None" = None,
           network_control: bool = True) -> SandboxReport:
    """샌드박스 안에서 실제로 시험을 돌리고 결과를 모은다.

    runner를 주면 그걸로 프로세스를 띄운다(테스트 주입용). 기본은 실제 실행.

    `escape_targets`: '작업공간 밖'이 무엇인지는 격리 방식마다 다르다.
      - 로컬 래퍼(bind mount): 작업공간의 **부모**가 밖이다(기본값).
      - 컨테이너: 부모는 대개 tmpfs라 써져도 무해하다. 진짜 밖은 시스템 경로
        (`/app`·`/etc`)다. 러너가 이걸 넘긴다.
    기본값을 컨테이너에 쓰면 "tmpfs에 썼다"는 이유로 멀쩡한 격리를 불합격시키고,
    반대로 컨테이너 기준을 로컬에 쓰면 실제 탈출을 놓친다 — 그래서 부르는 쪽이 정한다.

    `network_control`: 네트워크 시험에 **대조군**(샌드박스 밖에서도 나가지는지)을
    쓸지. 로컬 래퍼는 필요하다 — 오프라인 기계에서 "안에서 못 나갔다"는 격리의
    증거가 아니고, 그걸 통과로 적으면 격리 0인 래퍼에 합격을 준다(실측).
    러너처럼 **자기 자신이 곧 샌드박스**인 경우에는 밖이 없어서 대조군이 늘
    같은 결과를 낸다. 그때는 "지금 이 환경에서 나갈 수 없다"가 곧 필요한 보장이므로
    대조군을 끈다.
    """
    from .boundary import run_process

    run = runner or run_process
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    targets = tuple(escape_targets) if escape_targets else (str(_outside_target(root)),)

    # 네트워크 시험은 **대조군**이 필요하다. 샌드박스 밖에서도 연결이 안 되는
    # 기계라면(오프라인·사내망·CI) 안에서 실패한 것이 격리의 증거가 아니다.
    # 대조군 없이 '막힘=통과'로 적으면 격리가 0인 래퍼에 합격을 준다(실측).
    baseline = (_probe(run, _NO_SANDBOX, root, python, "network-baseline",
                       _NETWORK_PROBE, (), timeout, blocked_means=PASS)
                if network_control else None)
    if baseline is not None and baseline.status == PASS:   # 밖에서도 못 나간다
        network = ProbeResult(
            "network", UNKNOWN,
            "이 기계는 샌드박스 밖에서도 외부 연결이 안 됩니다 — 네트워크 격리를 "
            "이 시험으로는 증명할 수 없습니다(연결되는 환경에서 다시 확인하세요).")
    else:
        network = _probe(
            run, sandbox, root, python, "network", _NETWORK_PROBE, (), timeout,
            blocked_means=PASS,
            open_detail="샌드박스 안에서 외부 연결이 성공했다 — 네트워크가 열려 있다")
    results = [
        network,
        _escape_probe(run, sandbox, root, python, targets, timeout),
        _probe(run, sandbox, root, python, "workspace_writable",
               _INSIDE_PROBE, (str(root / ".until-probe"),), timeout,
               blocked_means=FAIL,
               open_detail="", blocked_detail=(
                   "작업공간 안에도 쓰지 못한다 — 이대로면 에이전트가 일을 못 한다")),
    ]
    return SandboxReport(tuple(results),
                         claimed_filesystem=bool(getattr(sandbox, "isolates_filesystem", False)),
                         claimed_network=bool(getattr(sandbox, "isolates_network", False)))


def _escape_probe(run, sandbox, root: Path, python: str,
                  targets: tuple, timeout: int) -> ProbeResult:
    """탈출 대상 중 **하나라도** 써지면 격리 실패로 본다."""
    unknown = None
    for target in targets:
        result = _probe(run, sandbox, root, python, "filesystem", _ESCAPE_PROBE,
                        (str(target),), timeout, blocked_means=PASS,
                        open_detail=f"작업공간 밖에 파일을 썼다 ({target}) — 격리되지 않았다")
        if result.status == FAIL:
            return result
        if result.status == UNKNOWN and unknown is None:
            unknown = result
    return unknown or ProbeResult("filesystem", PASS)


def _outside_target(root: Path) -> Path:
    """작업공간 밖의 쓰기 대상 — 부모 디렉터리에 임시 이름으로."""
    return root.parent / f".until-escape-{root.name}"


def _probe(run, sandbox, root: Path, python: str, name: str, code: str,
           extra_args: tuple, timeout: int, *, blocked_means: str,
           open_detail: str = "", blocked_detail: str = "") -> ProbeResult:
    # 코드를 `-c` 인라인으로 넘기지 않고 **파일로** 쓴다. 샌드박스 래퍼가 셸을
    # 거치면(예: `cmd /c`) 여러 줄 인라인 코드가 깨져 시험이 통째로 '모름'이
    # 된다 — 실측으로 확인. 파일 경로 인자 하나는 어떤 래퍼도 온전히 전달한다.
    script = root / f".until-probe-{name}.py"
    script.write_text(code, encoding="utf-8")
    argv = sandbox.wrap((python, str(script), *extra_args), root)
    try:
        result = run(argv, cwd=root, env=_PROBE_ENV, timeout=timeout)
    except Exception as exc:                       # 러너 자체가 던지면 '모름'
        return ProbeResult(name, UNKNOWN, f"시험을 돌리지 못했다: {exc}")
    if not getattr(result, "launched", True):
        return ProbeResult(name, UNKNOWN,
                           "샌드박스 안에서 파이썬을 찾지 못했다 — --python 으로 "
                           "샌드박스 안의 경로를 알려 주세요")
    if getattr(result, "timed_out", False):
        return ProbeResult(name, UNKNOWN, "시험이 시간 초과됐다")
    try:
        script.unlink()
    except OSError:
        pass
    out = (getattr(result, "stdout", "") or "").strip().splitlines()
    verdict = out[-1].strip() if out else ""
    if verdict == "BLOCKED":
        return ProbeResult(name, blocked_means, blocked_detail)
    if verdict == "OPEN":
        return ProbeResult(name, FAIL if blocked_means == PASS else PASS, open_detail)
    return ProbeResult(name, UNKNOWN,
                       "시험 결과를 읽지 못했다 — 샌드박스가 출력을 가로챘을 수 있다")
