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

하루 두 번(기본 08:00, 20:00) 메시지 하나가 텔레그램이나 파일로 온다. 설치 다음 날 아침에는 이런 모습이다(머리글과 블록 제목은 영문, 설치기가 큐에 넣는 항목은 설치 때 고른 보고 언어):

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
- 첫날에는 설치기가 큐에 넣은 설정 단계만 보인다. `cto 5h` 같은 입력 줄은 `config/report_daemon.yaml` 의 `inputs:` 에
  에이전트 상태 파일을 적은 뒤부터 나온다. 그전까지(처음에 숫자 파일이 없는 것은 정상이다) 두 번째 블록은 계획 모드로
  나온다: 회사 소개·인계 문서·큐의 열린 항목·첫 계획을 읽고 오늘 대표가 할 가장 급한 일, 역할별 진행 상황과 다음 행동,
  질문을 쓴다.
- 보고는 받는 곳과 무관하게 언제나 `state/report_daemon/reports/` 아래 파일로도 저장된다. 받는 곳이 `none`(기본값)이면
  아무것도 발송하지 않고 그 파일이 곧 보고다. `telegram` 이면 다시 읽을 수 있는 사본이다.
- 보고는 이 컴퓨터가 만든다. 보고 시각에 컴퓨터가 켜져 있고 잠자기 상태가 아니어야 한다(2절 용어 상자).

## 2. 빠른 시작(코딩 불필요)

준비물: Mac 또는 Linux, 설치 후 로그인까지 마친 [Claude Code](https://code.claude.com/docs/en/setup)(터미널에서 `claude` 를
입력하고, 물어보면 `/login` 을 입력해 브라우저 안내를 따른다), Python 3(Mac 에서 처음 `python3` 나 `git` 을 치면 Apple 명령행
도구 설치 창이 뜬다. 설치를 누른다). 터미널을 열고 아래 명령을 입력한다.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os my-company
cd my-company
python3 scripts/install.py
python3 scripts/first_plan.py
python3 scripts/report_daemon.py --once --dry-run
```

이 다섯 줄만으로는 내일 아침 보고가 오지 않는다. 보고는 서비스가 만들므로 3절(서비스 등록)까지 마친다.

시간·사용량: 첫 계획 생성 5~15분, 매일 보고 2회는 각각 1~3분, 모두 본인의 Claude 구독 사용량을 쓴다.

1. **받기.** 템플릿을 새 폴더 `my-company` 로 받는다. 이 폴더가 회사의 CEO 폴더가 된다.
   폴더는 clone 이 만듭니다. 미리 mkdir 하지 마세요(비어 있으면 무방).
   git 이 없으면 GitHub 에서 ZIP 을 받아 풀고, 그 폴더에서 터미널을 연다(아래 용어 상자).
2. **설치.** 질문 12개(작업 폴더를 등록하면 하나당 7개 더), 약 3분. 모든 질문에 [대괄호] 기본값이 있거나 Enter 가
   무엇을 하는지 적혀 있어서, 끝까지 Enter 만 눌러도 설치가 끝난다(작업 폴더 없이 설치되고, 설치기를 다시 실행해
   나중에 추가할 수 있다). 언제든 Ctrl+C 로 멈출 수 있고, 마지막 질문 전에는 설치 파일을 하나도 쓰지 않는다. 유일한 의존성
   PyYAML 이 없으면 설치를 제안한다. *보고를 어디로 받을까?* 에서 Enter 를 누르면 기본값 `none` 이다: 보고가 파일로 저장되고
   봇 토큰이 필요 없다. 텔레그램으로 받으려면 거기서 `telegram` 이라고 답하거나 나중에
   `python3 scripts/install.py --reconfigure delivery` 를 실행한다. 마지막에 점검을 돌리고 한 줄로 끝난다:
   `STATUS: COMPLETE`, 또는 고칠 항목을 나열한 `NOT COMPLETE`. 요약은 실제로 쓴 파일을 다시 읽어 보여주고, 3절의 서비스
   등록 명령을 이 컴퓨터의 경로로 채워 언제나 출력한다. `NOT COMPLETE` 여도 출력한다(먼저 나열된 항목을 고친다. 서비스
   등록은 지금 해도 된다).
3. **첫 계획.** CEO 에이전트가 파일(회사 소개·인계 문서·큐)을 읽고 2주 계획을 `advisory_drafts/<날짜>-first-plan.md` 에 쓴다:
   회사 현황 한 문단, 역할별 첫 2주 계획 3줄, 대표만 할 수 있는 일, 대표에게 묻는 질문. 대표 할 일 3~5건은 큐에 바로
   올라간다. CEO 가 준비를 마칠 때까지 `ready: false` 다(대표에게 묻는 사실 확인은 바로 ready). 쓸 수 있는 것은 그 파일과
   큐뿐이고 보호 구역은 절대 건드리지 않는다. 약 5~15분, 진행 중 메시지가 거의 없어도 정상이다. 끝나면 파일 경로, 큐에
   추가된 항목 수, 다음 명령을 보여준다.
4. **내일 아침 보고를 지금 보기.** 받게 될 보고를 화면에 출력한다(모델 호출 1회, 발송·기록 없음).
   `--no-model` 을 붙이면 상단 블록만 본다. `Not logged in` 이 나오면 `claude` 를 입력하고 `/login` 을 한 뒤 다시 실행한다.

같은 폴더에서 설치기를 다시 실행하면 먼저 묻는다: 1 그대로 유지(점검만 하고 실제 파일 내용을 보여줌), 2 질문에 다시 답하고
덮어쓰기(바뀌는 파일마다 `.bak` 사본, `HANDOFF.md` 와 큐는 바꾸지 않음), 3 중단. 보고 받는 곳만 바꾸려면
`python3 scripts/install.py --reconfigure delivery`, 타임존이나 보고 시각만 바꾸려면 `python3 scripts/install.py --reconfigure schedule`.

**보호 구역.** 설치기는 프로젝트마다 실제 돈이 움직이거나 라이브 서비스를 제어하는 파일·폴더(결제·가격·실거래·발송 작업 등)를
묻는다. 입력한 경로는 그 프로젝트 에이전트와 CEO 세션 양쪽의 절대경로 거부 규칙이 된다. 없는 경로는 확인하지 않으면 넣지 않고
(오타가 나면 진짜 폴더가 보호되지 않기 때문이다), 확인하지 않은 없는 보호 경로가 있으면 점검(doctor)이 실패한다. 프로젝트의
보호 구역을 비워 두려면 `none` 을 직접 입력해 확인해야 하고, 설치 요약에 **NO PROTECTED ZONE** 이 상자로 크게 표시된다.
프로젝트가 하나도 없으면 아직 보호할 것이 없으므로 이 단계는 한 줄 안내와 함께 건너뛰고, 프로젝트를 추가할 때 묻는다.
입력한 라이브 서비스 이름은 그 이름이 든 `launchctl`·`systemctl` 명령의 거부 규칙이 된다. 템플릿 자리표시자가 하나라도 남으면
설치는 완료가 아니다.

**질문 없이 설치.** `python3 scripts/install.py --answers answers.yaml` 은 답을 파일에서 읽는다. 형식은 설치할 때마다 생기는
`config/install_answers.yaml` 과 같다(`python3 scripts/install.py --help` 에 전체 예시가 있다). `--answers` 는 `allow_missing` 에
적은 경로만 확인한 것으로 본다. 그 밖의 보호 경로가 하나라도 없으면 파일을 하나도 쓰지 않고 멈춘다.

그다음 이 폴더에서 Claude Code 를 연다: `claude` 를 입력하고 Enter, 이어서 `recap` 을 입력한다(예: "recap 해 줘").
이제부터 할 일 목록은 `config/owner_action_queue.yaml` 이다(`open -e config/owner_action_queue.yaml` 로 연다). 남은 설정 단계는
설치기가 이미 거기에 올려 두었다. 하나를 끝내면 Claude Code 에서 CEO 에게 알린다(예: "서비스 등록 끝났어"). CEO 가
`status: done` 과 `done_at` 을 기록한다.
언제든 `python3 scripts/doctor.py` 로 설치 상태를 다시 점검한다(✓ 정상, ℹ 참고, ⚠ 읽어 볼 것, ✗ 고칠 것). 모델을 부르지 않고 Claude Code
로그인 여부까지 확인한다. `python3 scripts/doctor.py --check-login` 은 동의를 받은 뒤 Claude 에게 짧은 시험 메시지를 하나 보낸다.

- **프로젝트 에이전트는 도구가 적은 상태로 시작한다.** 제안된 허용 목록을 확정하기 전에는 각 에이전트가 자기 폴더의
  `.claude/settings*.json` 이 허용한 도구만 쓴다. 큐 항목이 할 일을 적어 둔다: 프로젝트 에이전트가 쓸 수 있는 도구 범위 확인.
  설치기가 제안한 목록(`config/logic_agents.yaml` 의 `allowed_tools`)을 읽고 괜찮으면 `permissions_status` 를 `confirmed` 로
  바꾸거나(`permissions_confirmed` 의 `date`·`by`·`quote`·`scope` 도 채운다. 비어 있으면 doctor 가 ✗), CEO 에이전트에게
  "허용 목록 확정"이라고 말한다. 그러면 CEO 가 원문·날짜·범위를 기록한다(아래 용어 상자의 예시).
- **사용량.** 프로젝트 에이전트의 기본값은 `model: opus`, `effort: high` 다(`config/logic_agents.yaml` 의 `defaults:`). 요금제가
  작으면 낮춘다.
- **회사 기록.** 회사 폴더는 템플릿의 사본으로 시작하므로 git 이 여전히 템플릿 저장소를 가리킨다. 회사 기록이 그쪽으로
  올라가지 않게 하려면 `git remote rename origin template` 을 실행한다.

**에이전트에게 맡기기.** Node.js 가 필요하다(`npx --version` 으로 확인하고, 없다고 나오면 nodejs.org 에서 먼저 설치한다).
회사 폴더에서 `npx skills add junhwanahn/c-level-agent-os` 를 실행한다. 질문이 나오면 Claude Code 와 **Project** 범위를 고른다
(Project 는 이 폴더에만 쓴다: 스킬 파일, `.claude/skills` 링크, `skills-lock.json`. Global 은 홈 폴더에 쓴다). 그다음 에이전트에게
*"우리 회사에 C-Level Agent OS 를 설치해 줘"* 라고 요청한다. 같은 질문을 묻고, 같은 설치기를 돌리고, 점검 결과를 보여준다.

> **용어 상자**
>
> - **절대경로**: `/` 로 시작하는 폴더의 전체 주소(예: `/Users/나/shop`), 또는 홈 폴더를 뜻하는 `~` 로 시작하는 주소(`~/shop`).
>   Finder 에서 폴더를 터미널 창으로 끌어다 놓으면 입력된다.
> - **폴더에서 터미널 열기**: `cd` 와 빈칸 하나를 입력하고, Finder 에서 폴더를 창으로 끌어다 놓은 뒤 Enter.
> - **Ctrl+C**: Control 을 누른 채 C. 실행 중인 명령을 멈춘다.
> - **Node.js**: `npx skills add` 에만 필요하다. 설치기에는 필요 없다.
> - **텔레그램 토큰과 chat id**: (1) 텔레그램에서 @BotFather 를 열고 `/newbot` 을 보내 받은 토큰을 복사한다. (2) 새로 만든 봇에게
>   아무 메시지나 보낸다. (3) 브라우저에서 `https://api.telegram.org/bot`, 토큰, `/getUpdates` 를 빈칸 없이 이어 붙인 주소를 열고
>   `"chat":{"id":` 뒤의 숫자를 복사한다. `{"ok":true,"result":[]}` 만 보이면 봇에게 메시지를 하나 더 보내고 새로고침한다.
> - **허용 목록 확정**: Claude Code 에서 예를 들어 *"shop 의 허용 목록을 확정한다. 자기 폴더 안의 파일은 고쳐도 된다."* 라고 말한다.
>   그러면 CEO 가 `config/logic_agents.yaml` 에 이렇게 기록한다:
>
>   ```yaml
>   permissions_status: confirmed
>   permissions_confirmed:
>     date: "2026-01-15"
>     by: "김민지"
>     quote: "shop 의 허용 목록을 확정한다. 자기 폴더 안의 파일은 고쳐도 된다."
>     scope: "shop: 제안된 allowed_tools 그대로"
>   ```
> - **보고 시각에 깨어 있기**: 보고는 이 컴퓨터에서 만들어지므로 보고 시각에 컴퓨터가 켜져 있고 잠자기 상태가 아니어야 한다.
>   잠들어 있었다면 깨어난 시점이 90분 안일 때는 그때 보낸다(`delayed` 표시). 그보다 늦으면 대신 `missed` 알림 하나가 온다.

## 3. 서비스 등록(보고가 스스로 오게)

설치기는 경로를 채운 서비스 파일을 `service/` 폴더에 써 두었고, 설치 요약 끝의 *3. 보고 서비스 등록* 아래에 이 컴퓨터용 명령을
그대로 출력했다(STATUS 줄과 무관하게). **그 줄들을 터미널에 그대로 붙여넣는다.** 큐의 대표 액션 `register_report_service_…` 에도 같은 명령이 들어 있다.
다시 출력하려면 `python3 scripts/install.py --render-service`.

붙여넣을 내용은 보고 받는 곳에 따라 다르다.

- **`none`(파일로만):** 보고는 `state/report_daemon/reports/` 아래 파일로만 저장되고, 채울 텔레그램 값이 없다. 나중에 텔레그램으로
  바꾸려면 `python3 scripts/install.py --reconfigure delivery` 를 실행해 `telegram` 이라고 답하고, 출력되는 명령을 붙여넣는다.
  `config/report_daemon.yaml`, 서비스 파일, 큐가 함께 바뀐다.
- **`telegram`:** 앞의 줄들이 서비스 파일을 복사하고 그 사본을 텍스트 편집기로 연다. `REPLACE_WITH_BOT_TOKEN` 과
  `REPLACE_WITH_CHAT_ID` 를 바꾸고(받는 법: 2절 용어 상자) 저장한 뒤 편집기를 닫고, 나머지 줄을 붙여넣는다. Linux 에서는
  `~/.config/report-daemon/env` 를 쓰는 줄에 `PASTE_TOKEN` 과 `PASTE_CHAT_ID` 가 있다. Enter 전에 둘 다 바꾼다.
  `python3 scripts/doctor.py` 는 등록된 사본(또는 env 파일)의 값을 확인한다. 자리표시자가 남아 있으면 ✗ 이고, 데몬은 발송하지
  않고 그 이유를 기록한다. 보고 본문은 그래도 `state/report_daemon/reports/` 에 저장된다.

macOS 에서 성공하면 이렇게 보인다(`plutil` 은 OK, `launchctl bootstrap` 은 아무것도 출력하지 않음, 상태는 running):

```text
/Users/you/Library/LaunchAgents/com.my-company.report-daemon.plist: OK
{"daemon": "running", "config": "...", "timezone": "Asia/Seoul", "now": "...", "report_times": ["08:00", "20:00"]}
```

자주 보는 오류:

```text
Bootstrap failed: 5: Input/output error
```

대개 서비스가 이미 등록돼 있다는 뜻이다(`python3 scripts/report_daemon.py --status` 가 `"daemon": "running"` 이면 정상). 또는
`~/Library/LaunchAgents/` 의 사본을 고치다 망가뜨린 경우다. `python3 scripts/install.py --render-service` 를 실행하고 출력되는
줄을 붙여넣는다. 옛 사본을 먼저 내린 뒤 다시 등록한다.

함정 네 개:

1. **서비스는 PATH 와 환경변수가 따로다.** launchd·systemd 는 `~/.zshrc`·`~/.bashrc` 를 읽지 않는다(nvm·pyenv·brew 설정 없음).
   설치기가 `python3`·`claude` 의 절대경로와 그 폴더를 담은 PATH 를 써 넣는다. 텔레그램 값은 서비스 정의(또는 systemd env 파일)에만
   두고 YAML 이나 git 에는 절대 넣지 않는다. 폴더를 옮기거나 Claude Code 를 다시 설치하면 설치기를 다시 돌린다.
2. **YAML 따옴표와 타임존.** 시각은 따옴표 친 문자열 `"08:00"` 이다(따옴표 없는 `20:00` 은 숫자 1200 으로 읽힌다).
   타임존은 `KST` 가 아니라 `Asia/Seoul` 같은 IANA 이름이다. 예약 `trigger_at` 에는 오프셋(`Z` 또는 `+09:00`)이 필요하다.
   `python3 scripts/report_daemon.py --check-config` 와 doctor 가 셋 다 잡는다.
3. **다시 읽히기.** macOS: 설치된 plist 를 고친 뒤 `launchctl kickstart -k` 는 *옛* 정의를 유지한다. 내렸다가 다시 등록한다
   (`--render-service` 가 출력하는 줄). Linux: 고칠 때마다 `systemctl --user daemon-reload`, 그리고 `loginctl enable-linger` 가 없으면
   로그아웃할 때 서비스가 멈춘다.
4. **붙여넣을 때 주석 금지.** macOS 터미널(zsh)은 `#` 를 주석으로 읽지 않는다. 명령 줄 뒤에 `#` 로 덧붙인 글은 명령의 일부가 된다.
   출력된 줄을 고치지 말고 그대로 붙여넣는다.

재시작은 보고 시각을 피해서 한다. 상세와 예약 트리거 규칙: [docs/05](docs/05-reporting-daemon.md#install-daemon-lite).

## 4. 수동 설치

개발자용, 또는 단계를 하나하나 보고 싶을 때. `TEMPLATE` 은 클론 경로이고, 나머지는 CEO 프로젝트 폴더에서 실행한다.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os ~/c-level-agent-os
TEMPLATE=~/c-level-agent-os
mkdir -p ~/my-company && cd ~/my-company && git init
mkdir -p config scripts tests .claude/agents state/report_daemon advisory_drafts/reviews
python3 -m pip install --user --disable-pip-version-check -r "$TEMPLATE/requirements.txt"
cp "$TEMPLATE"/templates/CLAUDE.md.ceo CLAUDE.md && cp "$TEMPLATE"/templates/HANDOFF.md HANDOFF.md
cp "$TEMPLATE"/templates/agents/*.md .claude/agents/
cp "$TEMPLATE"/templates/{owner_action_queue,logic_agents,report_daemon,access_control}.yaml config/
cp "$TEMPLATE"/templates/{report_prompt,autonomy_policy}.md config/
cp "$TEMPLATE"/scripts/{install,logic_agent,report_daemon,doctor,first_plan}.py scripts/
cp -R "$TEMPLATE"/templates .
cp "$TEMPLATE"/tests/{test_logic_agent,test_report_daemon,test_first_plan}.py tests/
cp "$TEMPLATE"/.gitignore .
```

유일한 의존성은 PyYAML 이다(macOS 에는 `pip` 명령이 없다. `python3 -m pip` 는 항상 된다). 쓰지 않을 역할은 `.claude/agents/` 에서
지운다. `templates/` 사본은 `--render-service` 와 `--reconfigure` 가 읽는다. `.gitignore` 는 `state/` 와 `service/` 를 git 에서 뺀다.

그다음 편집한다(모든 `{{...}}`. `UNIT_1`, `UNIT_2` 처럼 번호 붙은 것은 항목당 한 줄. `CLAUDE.md` 맨 위 주석은 지운다):

- `config/logic_agents.yaml`: 프로젝트 에이전트당 항목 하나. **키는 직접 정한 이름**이고 그 이름으로 부른다
  (예: `python3 scripts/logic_agent.py shop`). 템플릿의 `strategy_a`, `ops_bot` 은 예시다. 실제 `path`, `mode`, 그 폴더에 있는
  `handoff` 파일을 지정한다. 보호 구역: 크리티컬 존 경로를 모두 `denied_tools` 에 `Edit(//절대/경로/**)` 로 넣는다(슬래시 두 개 =
  절대경로, 한 개는 설정 위치 기준 상대경로. Claude Code 는 `Write(경로)` 규칙을 참조하지 않으므로 `Edit` 를 쓴다). 없다고 오너가
  확인한 경우에만 `protected_zone: none`. 아직 없는 경로는 그 에이전트의 `allow_missing:` 에도 적는다(아니면 doctor 가 실패).
  라이브 서비스는 `defaults.denied_tools` 에 `Bash(launchctl *이름*)`·`Bash(systemctl *이름*)`. 같은 규칙을 `.claude/settings.json` 에
  `{"permissions": {"deny": [...]}}` 로 넣어 CEO 세션도 막는다.
- `config/owner_action_queue.yaml`: 예시 항목(`example: true`)을 지운다.
- `config/report_daemon.yaml`: `timezone`, 따옴표 친 `report_times`, `claude_cmd` = `which claude` 출력,
  `delivery`(기본 `none`, 봇이 생기면 `telegram`).
- `config/autonomy_policy.md`, `config/access_control.yaml`, `HANDOFF.md`: 날짜, 대표 본인의 말, 경로.

서비스 파일을 쓰고, 점검한 뒤 첫 호출:

```bash
python3 scripts/install.py --render-service
python3 scripts/doctor.py
python3 scripts/logic_agent.py --status
python3 scripts/report_daemon.py --once --dry-run
```

- `--render-service` 는 `config/report_daemon.yaml` 로 `service/…` 를 쓰고 등록 명령을 출력한다(3절). doctor 출력에는 ✗ 가
  하나도 없어야 한다. `--status` 에 직접 정한 이름이 보이면 그중 하나로
  `python3 scripts/logic_agent.py shop -p "인계 문서를 읽고 현황을 5줄로 보고" --dry-run` 을 실행한다(`shop` 자리에 정한 이름).
- dry-run 출력의 `--disallowedTools` 에 실제 크리티컬 존 경로가 있어야 한다. 거부 규칙이 자리표시자로 남아 있으면 실행기는 실제 호출을 거부한다.
- 테스트는 선택이다: `python3 -m pip install --user -r requirements-dev.txt && python3 -m pytest tests -q`,
  pytest 없이 `python3 -m unittest discover tests`.
- `--dry-run` 을 빼면 Claude 를 호출한다. 오너가 허용 목록을 확정하기 전(`permissions_status: confirmed` +
  `permissions_confirmed` 의 날짜·확정자·원문·범위)에는 에이전트가 그 프로젝트의 `.claude/settings*.json` 이 허용한 도구만 쓴다.
  없으면 "Needed permission: …" 으로 끝나는 읽기 전용 보고를 예상한다. 프로젝트 에이전트는 CEO 에이전트가 직접 부른다.
  대표가 실행기를 손으로 돌릴 필요는 없다.
- 가상환경을 쓰려면: `python3 -m venv .venv && .venv/bin/python -m pip install pyyaml` 후 `--render-service` 를 포함해 어디서나
  `.venv/bin/python` 을 쓴다(서비스는 서비스 파일을 쓴 python 으로 실행된다).

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
                                  report_prompt.md, first_plan_prompt.md, autonomy_policy.md, access_control.yaml,
                                  HANDOFF.md, project_HANDOFF.md
templates/service/                보고 데몬용 launchd plist, systemd user unit
scripts/install.py                한 번에 끝나는 설치기(질문 또는 --answers 파일), --reconfigure,
                                  --render-service. 마지막에 점검
scripts/doctor.py                 설치 점검: 줄마다 ✓ / ℹ / ⚠ / ✗, ✗ 가 있으면 종료코드 1, --check-login
scripts/first_plan.py             첫 2주 계획과 대표 첫 할 일(큐)을 만드는 명령(모델 실행 1회)
scripts/logic_agent.py            headless 실행기
scripts/report_daemon.py          보고 데몬-lite: 2계층 보고, 예약 트리거, 텔레그램
tests/                            설치기·실행기·데몬 테스트(임시 폴더, stub CLI, 실제 호출 없음)
requirements.txt                  PyYAML(필수). requirements-dev.txt 는 pytest 추가(선택)
```

문서 본문, 템플릿, 스킬 파일은 영문입니다. 설치기의 질문·오류·요약 핵심 문구는 한국어 로캘이면 한국어로 나오고(`--lang en|ko`),
doctor 점검 줄은 영문이다.

## 6. 개발자가 아니라면: 유료 설치 및 30일 코칭

템플릿은 무료(MIT)입니다. 직접 운영하는 프로젝트에 설치하고 첫 30일 동안 함께 다듬는 서비스가 필요하면
**contact@crynomad.ai** · [crynomad.ai](https://crynomad.ai) 로 연락 주세요.

## 라이선스

MIT. [LICENSE](LICENSE) 참조.
