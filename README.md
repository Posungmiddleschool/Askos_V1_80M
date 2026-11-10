# Askos-V1-80M

바이트 단위 언어모델 Askos-V1-80M을 프롬 스크래치로 사전학습하는 프로젝트입니다. 두 발명을 씁니다.

- **커서:** 포스트 어텐션. 출처를 이어 쓰는 가설을 베이즈 규칙으로 갱신합니다.
- **새김:** 포스트 경사하강. 읽히지 않은 정보를 희소 기억에 한 번에 씁니다.

구상: Jungho Kwak (구현서 7판, 2026-11-09). 모든 단계는 Modal에서 실행합니다.

## 노트북 계획
| # | 노트북 | 하는 일 | GPU | 상태 |
|---|---|---|---|---|
| 00 | `00_setup_and_tests.ipynb` | 환경 점검: 패키지, Volume, HF, GPU 3종 | CPU + L4·L40S·H100 각 몇 분 | **통과** (2026-11-10, `RESULTS_LOG.md`) |
| 01 | `01_data_download.ipynb` | 소스 12개 다운로드(약 99GB 텍스트) → `reports/D1.md` | CPU 12컨테이너 | **코드 작성됨, Modal 실행 없음** |
| 02 | `02_data_clean_dedup_split` | 정제, 중복 제거, 벤치마크 제거, 분할 | L4 + CPU | 예정 |
| 03 | `03_library_index_anchors` | L3 색인, 앵커, 문서 해시, 로더 | CPU(대용량 RAM) | 예정 |
| 04 | `04_cursor_kernel` | 커서 numba·triton, T14~T20 | L4 | 예정 |
| 05 | `05_saegim` | 새김, T4·T5·T6·T21 | L40S | 예정 |
| 06 | `06_model_smoke` | 모델 + 스모크 30분 + 재개 시험 | L4 | 예정 |
| 07 | `07_ref_model` | 참조 모델 1시간 | L4 | 예정 |
| 08 | `08_preflight_fullsize` | 본 크기 속도·HBM 실측 | H100 | 예정 |
| 09 | `09_pretrain_main` | 본 사전학습 24 GPU시간 | H100 | 예정 |
| 10 | `10_eval_report` | 검증 BPB, `reports/P3.md` | H100 | 예정 |

## 실행 방법 (노트북 00)
1. **사람이 한 번:** `pip install modal && modal setup`, `modal secret create huggingface HF_TOKEN=<쓰기 토큰>`
2. `00_setup_and_tests.ipynb`를 위에서부터 실행합니다. 셸에서는 `bash launch.sh setup` 후 `bash status.sh setup`과 같습니다.
3. 결과는 `runs/00_setup/SETUP.md`와 HF `<user>/askos-v1-80m-state/00_setup/`에 남습니다.

에이전트에게 맡길 때는 `00_READ_ME_FIRST_AI_AGENT.md`와 `AGENT_INSTRUCTIONS.md`를 주면 됩니다.

## 상태 (정직하게)
- **00:** Modal에서 통과했습니다. 실측값은 `RESULTS_LOG.md`에 있습니다.
- **01 (코드만, Modal 실행 없음):**
  - 오프라인 시험 `tests/test_download.py` 15개 통과: 목표 배분, 컨테이너가 죽은 뒤 중복 없이 재개, 실패한 하위 소스 재시도, robots.txt, md5, 이어 받기, 파서·필터
  - 이 세션에서 실제 데이터로 작게 돌려 확인한 것:
    - DCLM 30MB: 상위 10% 기준값 0.525
    - github-code-clean: 라이선스 필터, 품질 기준값 0.565
    - FineMath, OpenWebMath, Proof-Pile-2 각 6MB
    - HF 소스 11개 파일 목록과 리비전
    - Gutenberg 카탈로그(책 823권 + Abbott)
    - lists.debian.org, irclogs, ConvoKit, Jargon File 4.4.7, man-pages 6.20, RFC 목록 8,873편
  - **안 돌려 본 것:** StackExchange(7z), BigQuery HN, OpenStax PDF 변환, 전체 규모 실행. 속도와 시간은 실측 전입니다.
  - LKML은 robots.txt가 막아서 받지 않습니다(NOTES.md D-14).
- 구현서에서 벗어난 결정은 `NOTES.md`에 있습니다(D-01~D-20).
