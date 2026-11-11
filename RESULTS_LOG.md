# RESULTS_LOG

| 날짜 | 버전 | 단계·아이디어 | 숫자 | 교훈 |
|---|---|---|---|---|
| 2026-11-10 | 00 설정 (`5e4fc3e`) | Modal·HF·GPU 3종 환경 점검 | H100 bf16 804 TFLOPS, L40S 210, L4 59; flash-attn varlen 오차 ≤ 0.010; H100 HBM 75GB 할당; Volume 쓰기 239·읽기 1,176 MB/s; HF 공개 읽기 108 MB/s | 3번째 실행에서 통과. 1번째: hf_xet extra 미설치. 2번째: 점검 코드의 import 위치 버그(환경은 정상) |
| 2026-11-11 | 01 다운로드 1차 (`07f1b25`, 계정 A) | 12개 소스 병렬 다운로드 | 5분 만에 parquet를 읽는 소스 전부 실패(fineweb_edu, korean_web, namuwiki, wikipedia 등 0GB): `ImportError: pyarrow requires NumPy 2.0 or newer, found 1.26.4` | 버전을 고정하지 않은 간접 의존성(pyarrow 26.0.1)이 고정된 numpy 1.26.4와 충돌. 00 점검은 버전만 보고 import는 하지 않았음 → 전체 lock(`requirements/image.lock.txt`), import 사전 점검(`setup_check/imports.py`) 추가 |
