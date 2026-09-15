# CodeNote 직원 호출 시스템

Render에 올려서 바로 테스트할 수 있는 반응형 웹앱입니다.

## 포함 기능

- 메인 화면: 고객 / 관리자 버튼 2개
- 고객 화면: 매장 검색, 매장 선택, 간단서비스 번호표 발급, 구매문의 번호표 발급
- 관리자 화면: 인증키 로그인, 매장 검색/선택, 갤럭시 컨설턴트 호출, 구매상담 호출
- 호출 기능: 호출, 재호출, 지정호출, 업무별 초기화
- 호출 화면: `/display` 또는 `/display?store_id=매장ID`
- 호출 팝업: 화면 중앙에 큰 번호 표시
- 음성 안내: `{번호}번 고객님. {업무명} 창구로 와주세요.`
- 매장 관리: 매장 생성, 매장 이름 수정, 삭제 처리, 검색
- ZIP 백업/복원: 매장 카테고리만 백업 및 복원
- 모바일/PC 반응형 UI
- 번호표 출력 앱 연동 위치 주석 포함: `app/static/js/app.js`의 `issueTicket()` 함수

## 업무 연결

| 고객 화면 | 관리자 화면 | 내부 코드 |
|---|---|---|
| 간단서비스 | 갤럭시 컨설턴트 | `simple_service` |
| 구매문의 | 구매상담 | `purchase_consult` |

## 관리자 인증키

기본 인증키는 `kyh`입니다.

Render 배포 시 환경변수 `ADMIN_KEY`를 원하는 값으로 바꾸세요.
여러 개를 쓰려면 쉼표로 구분합니다.

예시:

```text
ADMIN_KEY=kyh,manager123
```

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
6. 환경변수에 `ADMIN_KEY`를 등록합니다.
7. PostgreSQL을 사용할 경우 환경변수 `DATABASE_URL`을 등록합니다.

`DATABASE_URL`이 없으면 서버 내부 SQLite 파일 `staff_call.db`를 사용합니다. Render에서 장기 운영하려면 PostgreSQL 연결을 권장합니다.

Render가 최신 기본 Python을 쓰면 템플릿/의존성 동작이 달라질 수 있으므로, 이 프로젝트는 `.python-version`과 `render.yaml`에 Python 3.11.9를 지정했습니다.

## 주요 주소

```text
/          메인
/customer  고객 번호표 발급
/admin     관리자 호출/매장관리
/display   호출 표시 화면
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
