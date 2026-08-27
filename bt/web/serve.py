#!/usr/bin/env python3
"""
로컬 개발용 정적 파일 서버.

Web Bluetooth 는 보안 컨텍스트(https 또는 localhost)에서만 동작하므로
이 스크립트로 localhost 에 서빙한 뒤 http://localhost:8000 으로 연다.

    python serve.py           # 기본 8000 포트
    python serve.py 9000      # 포트 지정
"""
import http.server
import mimetypes
import sys
import os

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 파이썬 기본 테이블에 woff2 가 없어 application/octet-stream 으로 나간다.
# 실서버(nginx 등)에도 같은 매핑이 있어야 폰트 경고 없이 로드된다.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")

# ThreadingHTTPServer: 요청마다 스레드로 처리한다. 단일 스레드 서버는 브라우저의
# keep-alive 연결 하나에 물리면 다른 요청(새로고침 등)이 전부 막힌다.
with http.server.ThreadingHTTPServer(
    ("", PORT), http.server.SimpleHTTPRequestHandler
) as httpd:
    httpd.daemon_threads = True
    print(f"http://localhost:{PORT}  (Ctrl+C 로 종료)")
    print("Web Bluetooth 는 Chrome/Edge + localhost 또는 https 에서만 동작합니다.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
