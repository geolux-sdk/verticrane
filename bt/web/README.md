# Web Bluetooth 클라이언트 (PWA)

브라우저(Central)에서 라즈베리 파이(NUS Peripheral)로 BLE 송수신을 하는
설치형 **PWA**. 바닐라 HTML/JS + Web Bluetooth, 빌드 도구 없음.

## 파일

| 파일 | 내용 |
|---|---|
| `index.html` | UI + Web Bluetooth 로직 + PWA 등록 |
| `manifest.json` | 앱 이름·아이콘·독립창 설정 |
| `sw.js` | 서비스워커 — 앱 셸 오프라인 캐시 |
| `icons/` | 앱 아이콘 (192·512·maskable PNG) |
| `serve.py` | 개발·최초 설치용 정적 서버 (ThreadingHTTPServer) |
| `launcher.py` | 실행 파일용 런처 — 서버 + 브라우저 실행 + 종료까지 |
| `build_exe.py` | `launcher.py` 와 자산을 `VerticraneBT.exe` 로 굽는다 |

## 지원 범위

| 환경 | 지원 | 비고 |
|---|---|---|
| 데스크톱 Chrome / Edge | ✅ | `http://localhost` 또는 `https` |
| 안드로이드 Chrome | ✅ | `https` 필요 (또는 `adb reverse` 로 localhost) |
| iOS Safari | ❌ | Web Bluetooth 미지원 (이 프로젝트 대상 아님) |

> Web Bluetooth 는 **보안 컨텍스트**(https / localhost)에서만 동작. `file://` 로 열면 안 됨
> (기억된 기기 권한도 file:// 에선 저장 안 됨).

## 실행 파일 (현장 노트북용)

파이썬도, 명령줄도, Chrome 플래그 설정도 필요 없는 배포 형태다. **`VerticraneBT.exe`
를 더블클릭하면 앱 창이 뜨고, 그 창을 닫으면 끝난다.**

```bash
pip install pyinstaller pillow     # 굽는 PC 에만 필요
python build_exe.py                # → dist/VerticraneBT.exe  (약 8.4 MB)
python build_exe.py --clean        # build/ dist/ 를 지우고 처음부터
```

`dist/VerticraneBT.exe` 파일 하나를 노트북에 복사하면 된다. 빌드 산출물은
`.gitignore` 에 있으므로 필요할 때 다시 구우면 된다.

### 실행 파일이 알아서 하는 것

| | 왜 |
|---|---|
| Edge 또는 Chrome 을 직접 찾아 실행 | 기본 브라우저가 Firefox 면 Web Bluetooth 가 없어 첫 화면에서 멈춘다 |
| `--app=` 모드로 표시 | 주소창 없는 독립 창. PWA 설치와 같은 모양이다 |
| 위의 **Chrome 플래그 두 개**를 명령줄로 켬 | 사용자가 `chrome://flags` 를 뒤지지 않아도 장비 목록이 만들어진다 |
| 전용 프로필(`%LOCALAPPDATA%\VerticraneBT\browser`) | 플래그는 이미 떠 있는 브라우저에 주면 조용히 무시된다. 전용 프로필이라야 실제로 적용되고, 평소 쓰는 브라우저 설정도 건드리지 않는다 |
| 포트 8747 고정, `127.0.0.1` 로 접속 | 장비 권한과 기억된 목록은 **출처(origin)** 에 묶인다. 포트가 바뀌면 같은 앱이라도 기억이 사라진다 |
| 창을 닫으면 서버도 종료 | 창 없는 실행 파일에서 서버만 남아 도는 것을 막는다 |

한 번 허용한 BLE 장비는 그 전용 프로필에 남아 **다음 실행에도 목록에 뜬다.**

### 잘 안 될 때

- 화면 없이 오류 대화상자가 뜨면 그 내용과 `%LOCALAPPDATA%\VerticraneBT\launcher.log`
  를 본다. 로그는 매 실행마다 새로 쓴다.
- 8747 이 사용 중이면 8755 까지 위로 옮겨 뜬다. 이때는 **출처가 달라져 기억된 장비
  목록이 비어 보인다** (로그에 그 사실이 남는다). 8747 을 쓰던 프로그램을 끄고 다시
  실행하면 목록이 돌아온다.
- Edge/Chrome 이 아예 없으면 실행되지 않는다. Web Bluetooth 는 Chromium 계열
  전용이다.

## 실행 (데스크톱, 개발용)

실행 파일을 굽지 않고 소스에서 바로 볼 때 쓴다.

```bash
cd web
python serve.py            # http://localhost:8000
python launcher.py         # 또는: 브라우저까지 알아서 띄운다
```

Chrome/Edge 로 `http://localhost:8000` 접속 → 첫 화면이 **장비 목록** 이다.

- 처음이라면 **+ 새 장비 찾기** → 브라우저 선택창에서 `Pi-BLE-XXXX` 선택
- 한 번 고른 장비는 목록에 남는다(이름 · 마지막 사용 시각 · 마지막 IP) → 눌러서 연결
- 연결되면 Wi-Fi 설정 화면으로 넘어가고, 상단 **장비 목록** 으로 언제든 돌아온다

## PWA 설치 (Windows 앱처럼)

1. `http://localhost:8000` 에서 하드 새로고침(`Ctrl+Shift+R`) — 서비스워커 등록
2. 주소창의 **설치 아이콘** 클릭 (또는 메뉴 → 앱 → 이 사이트를 앱으로 설치)
3. 설치되면 블루투스 아이콘의 **독립 창 앱**으로 실행 (시작메뉴/바탕화면 등록)

> 설치 후엔 서비스워커 캐시 덕에 **`serve.py` 없이도 앱이 실행**된다(최초 설치 1회만
> 서버 필요). 단, 실제 연결하려면 Pi 의 `ble_peripheral.py` 는 켜져 있어야 한다.

## 장비 목록은 어떻게 만들어지나

한 현장에 장비가 여러 대이므로 **어느 장비를 설정할지 항상 사용자가 고른다.**
페이지를 열거나 화면에 돌아왔다고 예전 장비에 저절로 붙지 않는다.

Web Bluetooth 는 **앱이 직접 주변을 스캔해 목록을 그릴 수 없다**(`requestLEScan()` 은
실험 기능이라 플래그가 필요해 실사용 불가). 그래서 목록은 이렇게 만든다:

1. `navigator.bluetooth.getDevices()` — 이 브라우저가 **권한을 준** 장비들을 가져와
   카드로 나열한다. 이름 옆에 마지막 사용 시각과 마지막으로 본 IP 를 함께 적어,
   같은 이름의 장비가 여러 대여도 구분되게 한다(localStorage `pi-ble-device-memo`).
2. 처음 보는 장비는 **+ 새 장비 찾기** → `requestDevice()` 선택창을 한 번 거치면
   그 뒤로 목록에 남는다. 선택창에는 근처에서 광고 중인 `Pi-BLE-*` 가 모두 뜬다.
3. 연결 직전 `device.watchAdvertisements()` 로 실제 광고를 한 번 확인한다
   (안 하면 오래된 핸들로 `gatt.connect()` 가 "no longer in range" 로 실패).

**Chrome 플래그가 필요할 수 있음**(로그에 지원 여부가 출력됨):

- `chrome://flags/#enable-web-bluetooth-new-permissions-backend` → Enabled (getDevices)
- `chrome://flags/#enable-experimental-web-platform-features` → Enabled (watchAdvertisements)

`getDevices()` 를 못 쓰면 목록을 만들 수 없어 매번 **+ 새 장비 찾기** 를 거쳐야 한다.

**자동 재연결은 "지금 고른 장비" 에만 적용된다.** 설정 작업 도중 끊기면 그 장비로
계속 재시도하고, **장비 목록** 으로 나가거나 **끊기/중단** 을 누르면 멈춘다.

## 안드로이드 폰에서 테스트 (USB, adb)

```bash
adb reverse tcp:8000 tcp:8000     # 폰의 localhost:8000 -> PC:8000
python serve.py                    # PC 에서 서버 실행
```

폰 Chrome 에서 `http://localhost:8000` 접속. (폰 입장에서 localhost 라 보안 컨텍스트 충족)

## 안드로이드 네이티브 앱으로 확장 (Capacitor)

안드로이드 **WebView 는 Web Bluetooth 를 지원하지 않는다.** 네이티브 앱으로 만들려면
`@capacitor-community/bluetooth-le` 플러그인으로 BLE 호출부를 교체해야 한다
(이 플러그인은 **웹 백엔드도 있어** 같은 코드가 웹/안드로이드 양쪽에서 동작).

```bash
npm init -y
npm install @capacitor/core @capacitor/cli @capacitor/android @capacitor-community/bluetooth-le
npx cap init "PiBleTest" "com.example.piblet" --web-dir web
npx cap add android
npx cap sync
npx cap open android      # Android Studio 필요
```

교체 매핑: `navigator.bluetooth.requestDevice` → `BleClient.requestDevice`,
`characteristic.startNotifications` → `BleClient.startNotifications`,
`writeValueWithResponse` → `BleClient.write`. NUS UUID·UI 는 그대로.

`AndroidManifest.xml` 권한:

```xml
<uses-permission android:name="android.permission.BLUETOOTH_SCAN"
    android:usesPermissionFlags="neverForLocation" />
<uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
<!-- Android 11 이하 스캔용 -->
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"
    android:maxSdkVersion="30" />
```
