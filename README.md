# 더벨 News Clipper

더벨(thebell) 기사를 수집해 AI가 분류하고, PDF 합본과 DOCX 목차를 만들어 주는 데스크톱 도구입니다.
브라우저에서 동작하지만 전부 내 PC에서 실행되며, 기사 데이터가 외부로 나가지 않습니다.

---

## 실행 파일로 쓰기 (권장)

빌드된 `TheBellNewsClipper.exe` 를 원하는 폴더에 두고 더블클릭하면 끝입니다.
Python 설치도, 명령어도 필요 없습니다.

1. exe 실행 → 기본 브라우저가 자동으로 열립니다
2. 첫 화면에서 **Claude API 키**를 입력하고 저장
   ([console.anthropic.com](https://console.anthropic.com/settings/keys) 에서 발급)
3. 더벨 아이디/비밀번호는 선택 사항입니다 — 입력해 두면 자동 로그인, 비우면 브라우저에서 직접 로그인

실행하면 exe 옆에 다음이 생깁니다:

```
TheBellNewsClipper.exe
.env                # 설정 (설정 화면에서 자동 생성)
output/             # 생성된 PDF·DOCX·ZIP
browser_profile/    # Edge 로그인 상태 (팝업 '허용'을 기억)
```

폴더째 다른 PC로 옮겨도 설정이 그대로 따라갑니다.
포트 8000번이 사용 중이면 비어 있는 포트를 자동으로 찾습니다.

### 직접 빌드하기

Windows에서 `build.bat` 을 더블클릭하거나:

```bash
pip install -r requirements-build.txt
python build_exe.py            # dist/TheBellNewsClipper.exe (단일 파일, 약 55MB)
python build_exe.py --onedir   # 폴더 형태 (실행이 더 빠름)
```

빌드는 실행할 OS에서 해야 합니다 — Windows용 exe는 Windows에서 빌드하세요.

---

## 소스로 실행하기

```bash
pip install -r requirements.txt
python preflight.py       # 환경 점검 후 서버 실행까지
# 또는
python launcher.py
```

`.env` 는 없어도 됩니다. 없으면 브라우저 설정 화면이 먼저 열립니다.
미리 만들어 두려면 `.env.example` 을 복사해서 값을 채우세요.

---

## 사용 흐름

| 단계 | 화면 | 하는 일 |
|---|---|---|
| 1 | 대시보드 | 수집 기간을 정하고 기사 수집 시작 |
| 2 | 기사 선택 | AI 추천을 받거나 직접 고르기 |
| 3 | 목차 검수 | 드래그로 순서·분류 조정, 필요하면 AI 재분류 |
| 4 | 결과 | PDF 합본 · DOCX 목차 · 개별 PDF 를 ZIP으로 다운로드 |

---

## 요구 사항

- **Microsoft Edge** — 기사 수집에 사용합니다. 드라이버는 Selenium이 자동으로 관리합니다.
- **Claude API 키** — 기사 추천과 분류에 사용합니다.
- 소스로 실행할 경우 **Python 3.11 이상**

---

## 문제가 생기면

- 앱 안의 **환경 점검** 메뉴에서 상태를 확인할 수 있습니다.
- 콘솔 창에 로그가 그대로 찍히니, 오류 메시지를 확인한 뒤 창을 닫으세요.
- 설정을 바꾸려면 상단 **설정** 메뉴에서 다시 입력하면 됩니다. 재시작할 필요 없습니다.
