# C-Level Agent OS

### AI 경영진이 운영하는 1인 회사를 위한 운영 구조 하나.

**에이전트가 마지막 클릭 직전까지 전부 준비한다. 당신은 그 클릭만 — 돈이 움직이는 것은 에이전트가 손댈 몫이 아니다.**

[![MIT license](https://img.shields.io/badge/license-MIT-f4c430?style=flat-square)](LICENSE)
[![skills.sh](https://skills.sh/b/junhwanahn/c-level-agent-os)](https://skills.sh/junhwanahn/c-level-agent-os)

[English README](README.md)

CEO 에이전트, C-level 역할 에이전트(템플릿 4개 동봉: CTO·CRO·CFO·CMO — 전략 등 역할은 필요에 따라 추가), headless 로 호출하는
프로젝트 에이전트, 그리고 사람(오너)만 할 수 있는 액션을 모으는 큐 하나로 구성됩니다. Claude Code 용입니다.
1인 시스템 트레이딩 회사가 실제로 만들어 쓰는 구조에서 업종 고유 내용을 모두 걷어냈습니다.

## 1. 설치하면 무엇이 달라지나

하루 두 번(기본 08:00, 20:00) 메시지 하나가 텔레그램이나 파일로 온다. 설치 다음 날 아침에는 이런 모습이다(보고 본문은 영문):

```
[Report 2026-01-15 08:00 KST] on time
Inputs: queue 2h · cto 5h · cfo 31h STALE

> Owner actions 3 (actions 2, fact checks 1)
- Send reply to partner X (draft final, 6 lines) - 2026-01-15 (D-0) - external
- Approve OAuth consent for the video upload API (command prepared) - 2026-01-16 (D-1) - local
> Pending fact checks 1
- Did we already promise customer Y a delivery date? (yes/no + date) - 2026-01-17 (D-2) - facts

> Decided and executed by CEO (24h) 1
- Re-run analysis B with hypothesis 2 (hypothesis 1 rejected) (01/14)

> Due soon or overdue (D-2) 1
- [D-1] ceo - Weekly review: block budget per open topic, close or extend (2026-01-16)

> Summary: Two owner actions are due; the partner reply is final and waiting for your send
> Notable 2
- Analysis B re-run with hypothesis 2 is done; the result is in its handoff - (cto, 5h)
- The cfo input is older than its limit, so no cost statement is made - (cfo, STALE)
> CEO view
Nothing outside the queue needs you. Next CEO step: the weekly review tomorrow morning.
> Treated as stale: cfo
```

- 상단 블록은 파일 하나(`config/owner_action_queue.yaml`)에서 **모델 없이** 바로 렌더한다. 완화도 재정렬도 없다.
  이것이 할 일 목록 전부다. 여기에 없으면 대표가 할 일이 아니다.
- 대표 액션은 마지막 클릭 직전까지 준비돼 있다(초안·첨부·클릭 경로). 대표는 보내고, 결제하고, 서명하고, 답한다.
- 나머지는 CEO 에이전트가 결정·집행하고 두 번째 블록에 보고한다. 모든 입력에는 경과 시간이 붙는다.

## 2. 빠른 시작(코딩 불필요)

준비물: Mac 또는 Linux, 설치 후 로그인까지 마친 [Claude Code](https://code.claude.com/docs/en/setup)(`claude` 를 한 번 실행),
Python 3(Mac 에서 처음 `python3` 나 `git` 을 치면 Apple 명령행 도구 설치 창이 뜬다. 설치를 누른다).
터미널을 열고 명령 세 개를 입력한다.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os my-company && cd my-company
python3 scripts/install.py
python3 scripts/report_daemon.py --once --dry-run
```

1. **받기.** 템플릿을 새 폴더 `my-company` 로 받는다. 이 폴더가 회사의 CEO 폴더가 된다.
   git 이 없으면 GitHub 에서 ZIP 을 받아 풀고, 그 폴더에서 터미널을 연다.
2. **설치.** 평이한 질문 열 개 남짓. 모두 기본값이 있어 Enter 로 넘어갈 수 있다: 회사·대표 이름·타임존·보고 시각·
   메신저(`telegram`, 또는 우선 파일로만 받는 `none`)·역할·프로젝트 폴더. 유일한 의존성 PyYAML 이 없으면 설치를 제안한다.
   마지막에 점검을 돌리고 한 줄로 끝난다: `STATUS: COMPLETE`, 또는 고칠 항목을 나열한 `NOT COMPLETE`.
3. **내일 아침 보고를 지금 보기.** 받게 될 보고를 화면에 출력한다(모델 호출 1회, 발송·기록 없음).
   `--no-model` 을 붙이면 상단 블록만 본다.

**보호 구역.** 설치기는 프로젝트마다 실제 돈이 움직이거나 라이브 서비스를 제어하는 파일·폴더(결제·가격·실거래·발송 작업 등)를
묻는다. 입력한 경로는 그 프로젝트 에이전트와 CEO 세션 양쪽의 절대경로 거부 규칙이 된다. 비워 두려면 `none` 을 직접 입력해
확인해야 하고, 설치 요약에 **NO PROTECTED ZONE** 이 상자로 크게 표시된다. 템플릿 자리표시자가 하나라도 남으면 설치는 완료가 아니다.

그다음 이 폴더에서 Claude Code 를 열고(`claude`) recap 을 요청한다. 이제부터 할 일 목록은 `config/owner_action_queue.yaml` 이고,
남은 설정 단계는 설치기가 이미 거기에 올려 두었다. 언제든 `python3 scripts/doctor.py` 로 설치 상태를 다시 점검한다
(✓ 정상, ⚠ 읽어 볼 것, ✗ 고칠 것).

**에이전트에게 맡기기.** 회사 폴더에서 `npx skills add junhwanahn/c-level-agent-os` 를 실행한다. 질문이 나오면 Claude Code 와
**Project** 범위를 고른다(Project 는 이 폴더에만 쓴다: 스킬 파일, `.claude/skills` 링크, `skills-lock.json`. Global 은 홈 폴더에 쓴다).
그다음 에이전트에게 *"우리 회사에 C-Level Agent OS 를 설치해 줘"* 라고 요청한다. 같은 질문을 묻고, 같은 설치기를 돌리고, 점검 결과를 보여준다.

## 3. 서비스 등록(보고가 스스로 오게)

설치기가 경로를 채운 서비스 파일을 이미 써 두었다: macOS 는 `service/<label>.plist`, Linux 는 `service/report-daemon.service`.
큐의 대표 액션 `register_report_service_…` 에 이 컴퓨터용 명령이 그대로 들어 있다. 텔레그램: @BotFather 에서 `/newbot` 으로
토큰을 받고, 봇에게 메시지를 하나 보낸 뒤 `https://api.telegram.org/bot<token>/getUpdates` 에서 `chat.id` 를 복사한다.

macOS(launchd 사용자 에이전트, `<label>` 은 설치기가 출력한다. 예: `com.my-company.report-daemon`):

```bash
cp service/<label>.plist ~/Library/LaunchAgents/ && chmod 600 ~/Library/LaunchAgents/<label>.plist
open -e ~/Library/LaunchAgents/<label>.plist      # REPLACE_WITH_BOT_TOKEN, REPLACE_WITH_CHAT_ID 를 바꾸고 저장
plutil -lint ~/Library/LaunchAgents/<label>.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
python3 scripts/report_daemon.py --status          # "daemon": "running", 다음 보고 시각
```

Linux(systemd 사용자 유닛):

```bash
mkdir -p ~/.config/systemd/user ~/.config/report-daemon && cp service/report-daemon.service ~/.config/systemd/user/
printf 'REPORT_TELEGRAM_TOKEN=...\nREPORT_TELEGRAM_CHAT_ID=...\n' > ~/.config/report-daemon/env && chmod 600 ~/.config/report-daemon/env
systemctl --user daemon-reload && systemctl --user enable --now report-daemon
loginctl enable-linger "$USER"
python3 scripts/report_daemon.py --status
```

함정 세 개:

1. **서비스는 PATH 와 환경변수가 따로다.** launchd·systemd 는 `~/.zshrc`·`~/.bashrc` 를 읽지 않는다(nvm·pyenv·brew 설정 없음).
   설치기가 `python3`·`claude` 의 절대경로와 그 폴더를 담은 PATH 를 써 넣는다. 텔레그램 값은 서비스 정의(또는 systemd env 파일)에만
   두고 YAML 이나 git 에는 절대 넣지 않는다. 폴더를 옮기거나 Claude Code 를 다시 설치하면 설치기를 다시 돌린다.
2. **YAML 따옴표와 타임존.** 시각은 따옴표 친 문자열 `"08:00"` 이다(따옴표 없는 `20:00` 은 숫자 1200 으로 읽힌다).
   타임존은 `KST` 가 아니라 `Asia/Seoul` 같은 IANA 이름이다. 예약 `trigger_at` 에는 오프셋(`Z` 또는 `+09:00`)이 필요하다.
   `python3 scripts/report_daemon.py --check-config` 와 doctor 가 셋 다 잡는다.
3. **다시 읽히기.** macOS: 설치된 plist 를 고친 뒤 `launchctl kickstart -k` 는 *옛* 정의를 유지한다.
   `launchctl bootout gui/$(id -u)/<label>` 후 `bootstrap` 줄을 다시 실행한다. Linux: 고칠 때마다 `systemctl --user daemon-reload`,
   그리고 `loginctl enable-linger` 가 없으면 로그아웃할 때 서비스가 멈춘다.

재시작은 보고 시각을 피해서 한다. 상세와 예약 트리거 규칙: [docs/05](docs/05-reporting-daemon.md#install-daemon-lite).

## 4. 수동 설치

개발자용, 또는 단계를 하나하나 보고 싶을 때. `TEMPLATE` 은 클론 경로이고, 나머지는 CEO 프로젝트 폴더에서 실행한다.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os ~/c-level-agent-os && TEMPLATE=~/c-level-agent-os
mkdir -p ~/my-company && cd ~/my-company && git init
mkdir -p config scripts tests .claude/agents state/report_daemon advisory_drafts/reviews
python3 -m pip install --user -r "$TEMPLATE/requirements.txt"   # PyYAML. macOS 에는 `pip` 명령이 없다. 이 형태는 항상 된다
cp "$TEMPLATE"/templates/CLAUDE.md.ceo CLAUDE.md && cp "$TEMPLATE"/templates/HANDOFF.md HANDOFF.md
cp "$TEMPLATE"/templates/agents/*.md .claude/agents/          # 쓰지 않을 역할은 지운다
cp "$TEMPLATE"/templates/{owner_action_queue,logic_agents,report_daemon,access_control}.yaml config/
cp "$TEMPLATE"/templates/{report_prompt,autonomy_policy}.md config/
cp "$TEMPLATE"/scripts/{logic_agent,report_daemon,doctor}.py scripts/
cp "$TEMPLATE"/tests/test_logic_agent.py "$TEMPLATE"/tests/test_report_daemon.py tests/
cp "$TEMPLATE"/.gitignore .                                    # state/ 와 service/ 를 git 에서 뺀다
```

그다음 편집한다(모든 `{{...}}`. `UNIT_1`, `UNIT_2` 처럼 번호 붙은 것은 항목당 한 줄. `CLAUDE.md` 맨 위 주석은 지운다):

- `config/logic_agents.yaml`: 프로젝트 에이전트당 항목 하나. **키는 직접 정한 이름**이고 그 이름으로 부른다
  (`python3 scripts/logic_agent.py <정한 이름>`). 템플릿의 `strategy_a`, `ops_bot` 은 예시다. 실제 `path`, `mode`, 그 폴더에 있는
  `handoff` 파일을 지정한다. 보호 구역: 크리티컬 존 경로를 모두 `denied_tools` 에 `Edit(//절대/경로/**)` 로 넣는다(슬래시 두 개 =
  절대경로, 한 개는 설정 위치 기준 상대경로. Claude Code 는 `Write(경로)` 규칙을 참조하지 않으므로 `Edit` 를 쓴다). 없다고 오너가
  확인한 경우에만 `protected_zone: none`. 같은 규칙을 `.claude/settings.json` 에 `{"permissions": {"deny": [...]}}` 로 넣어 CEO 세션도 막는다.
- `config/owner_action_queue.yaml`: 예시 항목(`example: true`)을 지운다.
- `config/report_daemon.yaml`: `timezone`, 따옴표 친 `report_times`, `claude_cmd` = `which claude` 출력, `delivery`.
- `config/autonomy_policy.md`, `config/access_control.yaml`, `HANDOFF.md`: 날짜, 대표 본인의 말, 경로.

점검 후 첫 호출:

```bash
python3 scripts/doctor.py                     # ✗ 가 하나도 없어야 한다
python3 scripts/logic_agent.py --status       # 직접 정한 이름이 보인다
python3 scripts/logic_agent.py <이름> -p "인계 문서를 읽고 현황을 5줄로 보고" --dry-run
python3 scripts/report_daemon.py --once --dry-run
```

- dry-run 출력의 `--disallowedTools` 에 실제 크리티컬 존 경로가 있어야 한다. 거부 규칙이 자리표시자로 남아 있으면 실행기는 실제 호출을 거부한다.
- 테스트는 선택이다: `python3 -m pip install --user -r requirements-dev.txt && python3 -m pytest tests -q`,
  pytest 없이 `python3 -m unittest discover tests`.
- `--dry-run` 을 빼면 Claude 를 호출한다. 오너가 허용 목록을 확정하기 전(`permissions_status: confirmed` +
  `permissions_confirmed` 의 날짜·확정자·원문·범위)에는 에이전트가 그 프로젝트의 `.claude/settings*.json` 이 허용한 도구만 쓴다.
  없으면 "Needed permission: …" 으로 끝나는 읽기 전용 보고를 예상한다. 프로젝트 에이전트는 CEO 에이전트가 직접 부른다.
  대표가 실행기를 손으로 돌릴 필요는 없다.
- 가상환경을 쓰려면: `python3 -m venv .venv && .venv/bin/python -m pip install pyyaml` 후 서비스 파일을 포함해 어디서나 `.venv/bin/python` 을 쓴다.

## 5. 이것은 무엇이고, 무엇이 아닌가

- **규칙 체계.** 오너는 최종 액션만 한다. 오너의 손이 물리적으로 필요한 일(발송·결제·서명·인증·
  오너만 아는 사실)은 준비를 마친 상태로 큐 하나에 올라가고, 그 밖의 일은 CEO 에이전트가 결정·집행한 뒤
  보고한다. [docs/00](docs/00-philosophy.md) 참조.
- **템플릿**: CEO 헌장(`CLAUDE.md`), CTO/CRO/CFO/CMO 서브에이전트, 자율 운영 정책, 오너 액션 큐,
  프로젝트 에이전트 레지스트리, 접근 권한, 인계 로그.
- **설치기와 점검기** (`scripts/install.py`, `scripts/doctor.py`): 평이한 답변으로 템플릿을 채우고, 자리표시자가 남아 있는 한
  설치를 완료로 표시하지 않는다.
- **작은 실행기** (`scripts/logic_agent.py`, Python + PyYAML): 프로젝트 에이전트를 그 디렉터리에서
  headless 로 호출하고, 권한 경계를 유지하며, 세션을 이어 쓰거나 회전하고, 모든 호출을 기록한다.
- **보고 데몬-lite** (`scripts/report_daemon.py`, 같은 의존성): [docs/05](docs/05-reporting-daemon.md) 설계 노트를
  구현한다. 하루 2회 2계층 보고(LLM 없이 큐에서 렌더한 상단 블록 → 모델 호출 1회), 큐의 예약 트리거,
  텔레그램 전달, launchd 또는 systemd 로 상주.

이것이 아닌 것:

- **투자·법률·재무 자문이 아니다.** 무엇을 어떻게 거래하라는 내용은 없다.
- **에이전트에게 돈을 맡기는 방법이 아니다.** 결제·이체·자격증명·실자본에 대한 비가역 조치는 사람에게
  남겨 두도록 설계했다. 에이전트에게 자금 보관이나 결제 수단을 주지 말 것.
- 호스팅 제품도, 실행 프레임워크도 아니다. 상주하는 것은 직접 서비스 관리자에 등록하는 스크립트 하나(보고 데몬-lite)뿐이다.
  에이전트의 행동을 보증하지도 않는다. 권한 규칙은 위험을 줄일 뿐 없애지 않는다(에이전트가 실행한 스크립트는 여전히 파일을 쓸 수 있다).
  에이전트가 커밋한 내용은 직접 검토할 것.

<p align="center"><img src="docs/assets/approval-required-always.png" alt="크리티컬 존: 돈이 움직이는 파일은 모든 에이전트에게 거부, 사람 승인 항상" width="1200" /></p>

### 사례: 멈춤은 자동이다. 재개는 아니다

이 구조를 만든 회사는 라이브 전략 VOLCANO 를 사전등록 kill-switch 아래에서 운용했다. 2026-09-23
kill-switch 가 발동해 설계대로 집행됐다. 멈추는 데에는 누구의 결정도 필요 없었다.

그다음 에이전트가 한 일: 기록을 동결하고 공개 문안을 썼다. 하지 않은 일: 자본에 손대거나 무엇이든 재개하는 것.
둘 다 `approval_required: always` 로서 오너의 몫으로 남았다.

공개 기록: [crynomad.ai/track-record.html#volcano](https://crynomad.ai/track-record.html#volcano)

### 구성

```
.agents/skills/c-level-agent-os/  `npx skills add` 용 스킬: 에이전트가 설치기를 돌리고 점검 결과를 보여준다
docs/00-philosophy.md             오너는 최종 액션만 한다, 판단 질문 하나
docs/01-ceo-charter.md            헌장 구성, 책임 매트릭스 골격
docs/02-autonomy-policy.md        개선 루프, 블록 예산, 지속성
docs/03-owner-action-queue.md     큐 스키마, 렌더 규칙, 예시
docs/04-logic-agents-headless.md  레지스트리, 권한·경로 규칙, 세션 회전, 라이브·headless 동시 금지
docs/05-reporting-daemon.md       2계층 정기보고, 예약 트리거(설계 노트, 설치)
templates/                        CLAUDE.md.ceo, agents/, owner_action_queue.yaml, logic_agents.yaml, report_daemon.yaml,
                                  report_prompt.md, autonomy_policy.md, access_control.yaml, HANDOFF.md
templates/service/                보고 데몬용 launchd plist, systemd user unit
scripts/install.py                한 번에 끝나는 설치기(질문 또는 --answers 파일), 마지막에 점검
scripts/doctor.py                 설치 점검: 줄마다 ✓ / ⚠ / ✗, ✗ 가 있으면 종료코드 1
scripts/logic_agent.py            headless 실행기
scripts/report_daemon.py          보고 데몬-lite: 2계층 보고, 예약 트리거, 텔레그램
tests/                            설치기·실행기·데몬 테스트(임시 폴더, stub CLI, 실제 호출 없음)
requirements.txt                  PyYAML(필수). requirements-dev.txt 는 pytest 추가(선택)
```

문서 본문, 템플릿, 스킬 파일은 영문입니다. 설치기 질문은 한국어 로캘이면 한국어로 나온다(`--lang en|ko`).

## 6. 개발자가 아니라면: 유료 설치 및 30일 코칭

템플릿은 무료(MIT)입니다. 직접 운영하는 프로젝트에 설치하고 첫 30일 동안 함께 다듬는 서비스가 필요하면
**contact@crynomad.ai** · [crynomad.ai](https://crynomad.ai) 로 연락 주세요.

## 라이선스

MIT. [LICENSE](LICENSE) 참조.
