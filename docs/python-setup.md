# Python 환경 설정 가이드

## pip 업그레이드

```bash
python -m pip install --upgrade pip
```

---

## 라이브러리 설치

### 일반 환경 (개인 PC / 외부 서버)

```bash
pip install -r requirements.txt
```

### 사내 네트워크 / 클라우드 환경 (SSL 인증서 오류 발생 시)

사내 프록시가 자체서명 인증서를 사용하는 경우 `--trusted-host` 옵션이 필요하다.

```bash
pip install -r requirements.txt \
  --trusted-host pypi.org \
  --trusted-host files.pythonhosted.org \
  --trusted-host pypi.python.org
```

> **증상:** `SSL: CERTIFICATE_VERIFY_FAILED` 또는 `certificate verify failed: self-signed certificate in certificate chain`

---

## Playwright 브라우저 설치 (필수)

`pip install -r requirements.txt` 만으로는 브라우저 바이너리가 설치되지 않는다.  
Playwright는 Python 라이브러리와 브라우저 실행 파일을 분리 배포하며,  
바이너리는 OS 캐시 디렉터리(`~/Library/Caches/ms-playwright` 등)에 별도 저장된다.

설치하지 않으면 headless 렌더링 시 아래 오류가 발생한다.

```
playwright._impl._errors.Error: BrowserType.launch: Executable doesn't exist
```

venv가 활성화된 상태에서 아래 명령을 실행한다.

```bash
playwright install chromium
```

> venv를 새로 만들어도 재설치 불필요. 단, Playwright 버전 업그레이드 시에는 재실행해야 한다.

---

## 앱 실행 시 SSL 설정

사내 프록시 환경에서는 HTTP 요청에도 SSL 검증 오류가 발생한다.  
`.env.local` 에 아래 항목을 추가한다.

```
HTTP_VERIFY_SSL=false  # 사내 프록시 자체서명 인증서로 인한 SSL 검증 비활성화
```

외부 서버(Ubuntu 등) 에서는 설정하지 않거나 `true` 로 유지한다.
