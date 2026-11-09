# Askos-V1-80M

바이트 단위 언어모델 Askos-V1-80M을 프롬 스크래치로 사전학습하는 프로젝트입니다. 두 발명을 씁니다.

- **커서:** 포스트 어텐션. 출처를 이어 쓰는 가설을 베이즈 규칙으로 갱신합니다.
- **새김:** 포스트 경사하강. 읽히지 않은 정보를 희소 기억에 한 번에 씁니다.

구상: Jungho Kwak (구현서 7판, 2026-11-09). 모든 단계는 Modal에서 실행합니다.

## 노트북 계획
| # | 노트북 | 하는 일 | GPU | 상태 |
|---|---|---|---|---|
| 00 | `00_setup_and_tests.ipynb` | 환경 점검: 패키지, Volume, HF, GPU 3종 | CPU + L4·L40S·H100 각 몇 분 | **코드 작성됨, Modal 실행 없음** |
| 01 | `01_data_download` | 소스 13개 다운로드 | CPU | 예정 |
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
- **시험한 것:**
  - `tests/test_infra.py` 9개 통과(설정 일관성, 소스 비율, 스모크 병합, 토큰 가리기, `modal_app.py`와 설정의 이름 일치, 저장소에 토큰 없음)
  - 모든 파이썬 파일 `py_compile` 통과, 셸 스크립트 `bash -n` 통과
  - 점검 틀(`setup_check.Checks`)을 로컬에서 건식 실행
- **아직 안 돌려 본 것:**
  - Modal 실행 없음: 이미지 빌드, `deploy`, `spawn`, Volume, HF 업로드 모두
  - GPU 실행 없음: flash-attn, triton, HBM 점검
  - 숫자(TFLOPS, MB/s)는 아직 하나도 없습니다. 실행하면 실측값만 기록됩니다.
- 구현서에서 벗어난 결정은 `NOTES.md`에 있습니다.
