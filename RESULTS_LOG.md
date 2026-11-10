# RESULTS_LOG

| 날짜 | 버전 | 단계·아이디어 | 숫자 | 교훈 |
|---|---|---|---|---|
| 2026-11-10 | 00 설정 (`5e4fc3e`) | Modal·HF·GPU 3종 환경 점검 | H100 bf16 804 TFLOPS, L40S 210, L4 59; flash-attn varlen 오차 ≤ 0.010; H100 HBM 75GB 할당; Volume 쓰기 239·읽기 1,176 MB/s; HF 공개 읽기 108 MB/s | 3번째 실행에서 통과. 1번째: hf_xet extra 미설치. 2번째: 점검 코드의 import 위치 버그(환경은 정상) |
