# Raspberry Pi 이식 가이드

HWT9037-485 틸트 센서 도구를 라즈베리파이에서 실행하기 위한 설정 안내입니다.
연결 방식은 **USB-RS485 어댑터**(예: FTDI/CH340 칩셋)를 기준으로 합니다.

## 1. 의존성 설치 (가상환경)

최근 라즈베리파이 OS(Bookworm 이후)는 PEP 668 정책으로 시스템 파이썬에 직접
pip 설치를 막습니다(`externally-managed-environment` 오류). 그래서
`install_requirements.sh`는 프로젝트 안에 가상환경 `.venv`를 만들고 거기에
설치합니다. **`sudo`로 실행하지 마세요** — venv는 사용자 소유여야 합니다.

```bash
cd ~/verticrane
chmod +x install_requirements.sh run_dashboard.sh   # 실행 권한 (한 번만)

# venv 도구가 없으면 먼저 설치
sudo apt update && sudo apt install -y python3-venv python3-full

./install_requirements.sh        # .venv 생성 + 의존성 설치 (sudo 없이!)
```

라즈베리파이(ARM)에서는 pip이 [piwheels](https://www.piwheels.org/)의 미리 빌드된
휠을 사용하므로 `numpy`, `pandas`, `streamlit` 등이 소스 빌드 없이 빠르게 설치됩니다.

> 이후 모든 스크립트는 venv의 파이썬으로 실행합니다: `.venv/bin/python <스크립트>`
> 또는 `source .venv/bin/activate`로 활성화한 뒤 `python <스크립트>`.

## 2. 시리얼 포트 권한

USB-RS485 어댑터는 보통 `/dev/ttyUSB0`(FTDI/CH340) 또는 `/dev/ttyACM0`으로
잡힙니다. 시리얼 포트에 접근하려면 사용자를 `dialout` 그룹에 추가해야 합니다:

```bash
sudo usermod -aG dialout $USER
# 로그아웃 후 다시 로그인(또는 재부팅)해야 그룹 변경이 적용됩니다.
```

연결된 포트 확인:

```bash
ls -l /dev/serial/by-id/        # 어댑터별 안정적인 심볼릭 링크
dmesg | grep -i tty             # 방금 꽂은 어댑터가 ttyUSB0/ttyACM0 중 무엇인지
```

## 3. 포트 지정 방법

모든 스크립트는 다음 순서로 포트를 결정합니다:

1. `--port` 명령행 인자
2. `VERTICRANE_PORT` 환경변수
3. USB 시리얼 어댑터 자동 감지 (pyserial)
4. 플랫폼 기본값 (Linux `/dev/ttyUSB0`, Windows `COM11`)

### 예시

아래는 venv를 활성화(`source .venv/bin/activate`)한 상태를 가정합니다.
활성화하지 않았다면 `python` 대신 `.venv/bin/python`을 쓰세요.

```bash
# 자동 감지에 맡기기 (어댑터 1개만 연결된 경우 가장 간단)
python read_status.py

# 명시적으로 포트 지정
python read_status.py --port /dev/ttyUSB0
python configure_sensor.py --port /dev/ttyUSB0
python test.py --port /dev/ttyUSB0

# 분(minutes)과 포트를 함께 지정
python log_tilt.py 10 --port /dev/ttyUSB0

# 환경변수로 한 번만 지정 (현재 셸 세션 동안 유지)
export VERTICRANE_PORT=/dev/ttyUSB0
python log_tilt.py 10

# 활성화 없이 venv 파이썬 직접 호출
.venv/bin/python read_status.py --port /dev/ttyUSB0
```

> 어댑터를 안정적으로 가리키려면 `/dev/serial/by-id/...` 경로를 그대로
> `--port` 또는 `VERTICRANE_PORT`에 넣는 것을 권장합니다. `ttyUSB0` 번호는
> 여러 USB 장치를 꽂으면 바뀔 수 있습니다.

## 4. 대시보드 실행

```bash
./run_dashboard.sh
# 브라우저에서 http://<라즈베리파이IP>:8501 접속
```

`run_dashboard.sh`는 `.venv`가 있으면 자동으로 그 파이썬을 사용합니다.

원격에서 접속하려면 라즈베리파이 외부에서 보이도록 실행:

```bash
.venv/bin/python -m streamlit run dashboard.py --server.address 0.0.0.0
```
