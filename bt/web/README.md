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

## 지원 범위

| 환경 | 지원 | 비고 |
|---|---|---|
| 데스크톱 Chrome / Edge | ✅ | `http://localhost` 또는 `https` |
| 안드로이드 Chrome | ✅ | `https` 필요 (또는 `adb reverse` 로 localhost) |
| iOS Safari | ❌ | Web Bluetooth 미지원 (이 프로젝트 대상 아님) |

> Web Bluetooth 는 **보안 컨텍스트**(https / localhost)에서만 동작. `file://` 로 열면 안 됨
> (기억된 기기 권한도 file:// 에선 저장 안 됨).

## 실행 (데스크톱)

```bash
cd web
python serve.py            # http://localhost:8000
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
