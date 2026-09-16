# CodeNote 직원 호출 시스템

Render에 올려서 바로 테스트할 수 있는 반응형 웹앱입니다.

## 포함 기능

- 메인 화면: 고객 / 관리자 버튼 2개
- 고객 화면: 매장 검색, 매장 선택, 간단서비스/구매문의 업무 선택, 선택한 업무의 호출 표시
- 관리자 화면: CodeNote 인증키 로그인, 매장 검색/선택, 갤럭시 컨설턴트/구매상담 업무 선택, 선택한 업무만 호출
- 호출 기능: 호출, 재호출, 지정호출, 업무별 초기화
- 고객 호출 화면: `/customer` 또는 `/customer?store_id=매장ID&service_type=업무코드`
- 호출 팝업: 고객 호출 화면 중앙에 큰 번호 표시
- 음성 안내: `{번호}번 고객님. {업무명} 창구로 와주세요.`
- 호출번호 표시: 호출 시 선택한 고객 화면에 번호 표시, 팝업이 사라진 뒤 5초 후 `대기중`으로 복귀
- 대기 목록: 고객 호출 화면에서만 표시
- 매장 관리: 매장 생성, 매장 이름 수정, 삭제 처리, 검색
- ZIP 백업/복원: 매장 카테고리만 백업 및 복원
- 모바일/PC 반응형 UI
- 번호표 출력 앱 연동 위치 주석 포함: `app/static/js/app.js`의 `issueTicketForApp()` 함수
- 관리자 브라우저 푸시 알림: 관리자 로그인 후 매장을 선택하고 `발급 알림 켜기`를 누르면 해당 매장 번호표 발급 시 삼성인터넷/지원 브라우저에 알림 표시

## 업무 연결

| 고객 화면 | 관리자 화면 | 내부 코드 |
|---|---|---|
| 간단서비스 | 갤럭시 컨설턴트 | `simple_service` |
| 구매문의 | 구매상담 | `purchase_consult` |

## 관리자 인증키

관리자 인증은 CodeNote 인증키 서버를 사용합니다.
인증 요청은 기본값으로 `https://poketserver.onrender.com/app/check`에 `{"code":"인증키"}` 형식으로 전달합니다.
인증 서버 응답의 `status`가 `approved`이고 `token` 값이 있을 때만 관리자 세션을 발급합니다.

인증키는 이 대기 서버에 저장하지 않습니다. CodeNote 인증 서버에서 활성화된 키로 확인되면 계속 로그인할 수 있습니다.

## 브라우저 푸시 알림 설정

푸시 알림은 Web Push 방식입니다. Render 배포 후 아래 환경변수가 있어야 실제 백그라운드 알림이 발송됩니다.

```text
VAPID_PUBLIC_KEY=생성된 공개키
VAPID_PRIVATE_KEY=생성된 개인키
VAPID_SUBJECT=mailto:code_note95@naver.com
```

키 생성은 로컬에서 아래 명령어로 합니다.

```bash
pip install -r requirements.txt
python scripts/generate_vapid_keys.py
```

출력된 3개 값을 Render Environment Variables에 그대로 등록합니다.

관리자 사용 흐름은 아래와 같습니다.

```text
관리자 로그인
→ 매장 선택
→ 발급 알림 켜기
→ 브라우저 알림 권한 허용
→ 해당 매장에서 키오스크 번호표가 발급되면 알림 수신
```

같은 브라우저에서 다른 매장을 선택하고 다시 `발급 알림 켜기`를 누르면 알림 대상 매장이 새로 선택한 매장으로 변경됩니다. 로그아웃 버튼을 누르면 현재 브라우저의 푸시 구독을 해제합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

실행 후 접속:

```text
http://127.0.0.1:8000
```

## Render 배포

1. 이 폴더 전체를 GitHub 저장소에 올립니다.
2. Render에서 New Web Service를 생성합니다.
3. Build Command는 아래 값으로 둡니다.

```bash
pip install -r requirements.txt
```

4. Start Command는 아래 값으로 둡니다.

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

5. 환경변수에 `PYTHON_VERSION=3.11.9`를 등록합니다.
6. PostgreSQL을 사용할 경우 환경변수 `DATABASE_URL`을 등록합니다.
7. 푸시 알림을 사용할 경우 `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`를 등록합니다.

`DATABASE_URL`이 없으면 서버 내부 SQLite 파일 `staff_call.db`를 사용합니다. Render에서 장기 운영하려면 PostgreSQL 연결을 권장합니다.

Render가 최신 기본 Python을 쓰면 템플릿/의존성 동작이 달라질 수 있으므로, 이 프로젝트는 `.python-version`과 `render.yaml`에 Python 3.11.9를 지정했습니다.

## 주요 주소

```text
/          메인
/customer  고객 호출 화면
/admin     관리자 호출/매장관리
/display   고객 호출 화면 보조 주소
/sw.js     브라우저 푸시용 Service Worker
/healthz   상태 확인
```

## 백업/복원 위치

관리자 로그인 후 오른쪽 상단의 `⚙ 매장관리` 버튼을 누릅니다.
인증키를 다시 입력하면 아래 기능을 사용할 수 있습니다.

- ZIP 백업 다운로드
- ZIP 복원
- 매장 생성
- 매장 이름 수정
- 매장 삭제 처리
- 비활성 매장 복구

백업 ZIP에는 매장 카테고리만 들어갑니다. 번호표, 현재 호출번호, 호출기록, 업무별 카운터는 백업하지 않습니다. 복원 시 백업에 없는 매장은 삭제하지 않고 비활성화해서 기존 번호표/호출기록이 사라지지 않도록 했습니다.

## 초기화 기준

파란색 갤럭시 컨설턴트 초기화는 간단서비스 번호만 초기화합니다.
빨간색 구매상담 초기화는 구매문의 번호만 초기화합니다.
초기화 후 다음 번호표는 1번부터 다시 시작합니다.
