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

### 코드 보호

기본 빌드는 배포용으로 안전하게 하드닝됩니다:

- **소스(.py) 미포함** — 앱 코드는 바이트코드로만 들어가며, exe를 압축 해제해도 `.py` 파일이 나오지 않습니다.
- **-OO 컴파일** — docstring과 assert가 바이트코드에서 제거됩니다.
- **심볼 제거** — 번들 라이브러리의 심볼을 없앱니다.

이것으로 "exe를 열어 코드를 그대로 읽는" 것은 막힙니다. 실제로 표준 도구(pyinstxtractor + decompyle3)로 뜯어보면 소스는 0개, 바이트코드만 나오고 그마저도 Python 3.11 디컴파일은 실패합니다.

다만 **순수 파이썬은 완벽한 역공학 방지가 불가능합니다.** 결정적인 사람은 바이트코드 수준의 로직을 근사 복원할 수 있습니다. 상용 수준의 난독화가 필요하면:

```bash
pip install pyarmor
pyarmor reg <구입한_라이선스>     # 체험판은 배포 불가
python build_exe.py --obfuscate
```

PyArmor는 코드를 네이티브 런타임(.so/.pyd)으로 감싸 바이트코드 복원을 실질적으로 차단합니다. 정식 라이선스면 `app` 패키지 전체를 난독화할 수 있습니다.

**체험판으로 테스트** (배포 금지, 개인용):

```bash
pip install pyarmor
python build_exe.py --obfuscate --allow-trial
```

체험판은 누적 ~40KB 코드 한도가 있어 전체는 난독화하지 못합니다. 그래서 `build_exe.py`의 `OBF_FILES`는 기본적으로 **핵심 IP인 `llm_classifier.py`(분류 프롬프트·로직)만** 난독화하고, 나머지는 하드닝 바이트코드로 둡니다. 정식 라이선스를 등록하면 이 목록을 패키지 전체로 넓히면 됩니다. 배포용은 `--allow-trial` 없이 정식 라이선스로 빌드하세요.

---

## 소스로 실행하기 (제일 쉬움)

**`실행.bat` 을 더블클릭하세요.** 그게 전부입니다.

처음 실행하면 자동으로:

1. Python 확인
2. 가상환경(.venv) 생성
3. 필요한 프로그램 설치
4. 앱 실행 + 브라우저 자동 열기

두 번째부터는 1~3을 건너뛰고 바로 실행됩니다(`requirements.txt` 가 바뀌면 그때만 다시 설치). Python 3.11 이상만 미리 깔려 있으면 됩니다 — 없으면 배치가 안내해 줍니다.

`.env` 는 없어도 됩니다. 없으면 브라우저 설정 화면이 먼저 열려 API 키를 입력받습니다.

### 직접 명령어로 실행 (선택)

```bash
pip install -r requirements.txt
python launcher.py
```

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
