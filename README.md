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

## 여러 Modal 계정으로 함께 하기 (NOTES.md D-23)
Modal Volume은 계정끼리 공유되지 않습니다. 그래서 **HF 계정 하나(`AskosAILabs`)**를 공유 기준 저장소로 쓰고, 각 계정의 Volume은 캐시로만 씁니다.

**사람이 한 번 할 일:**
1. 공유 HF 계정은 `configs/infra.yaml`의 `hub.namespace: AskosAILabs`로 이미 정해 두었습니다.
2. `AskosAILabs` 계정에서 **사람마다 따로** 쓰기 토큰을 만듭니다(Settings → Access Tokens, 이름 예: `modal-alice`, `modal-bob`). 같은 토큰 하나를 복사해 나눠 쓰지 않아야, 한 사람의 토큰만 폐기할 수 있습니다.
3. 각자 자기 Modal 계정에서 `modal setup`을 하고, 2에서 받은 자기 토큰으로 `modal secret create huggingface HF_TOKEN=<토큰>`을 만듭니다.
   - 잠금은 Modal 계정 이름으로 누가 했는지 구별합니다. `python3 ops.py whoami`에 두 사람의 계정 이름이 같게 나오면, 한 사람은 `export ASKOS_ACCOUNT=<다른 이름>`을 쓰세요.
4. 계정마다 노트북 00을 한 번씩 돌립니다. `hf_shared_repos`와 `shared_namespace`가 통과하면 준비가 끝난 것입니다.

**순서: A가 전부, A의 크레딧이 떨어지면 B가 이어받기**
1. **계정 A**가 모든 단계를 그냥 실행합니다(`bash launch.sh download`, 소스 12개 전부). 진행 상황은 20분마다 HF에 올라갑니다.
2. A의 무료 크레딧이 떨어져 작업이 멈추면(Modal에서 크레딧·결제 오류, 컨테이너가 죽음), A의 에이전트는 **보고만** 합니다.
3. 사람이 확인한 뒤 **계정 B**의 에이전트에게 `bash launch.sh download --takeover`를 시킵니다.
   - B는 A가 끝낸 소스는 건너뜁니다.
   - A가 하다 멈춘 소스는 HF에 마지막으로 올라간 지점부터 이어 받습니다. 잃는 양은 최대 20분어치입니다.
   - A의 컨테이너가 혹시 아직 살아 있으면, 10분 안에 잠금을 잃은 것을 알아차리고 스스로 멈춥니다. 업로드도 하지 않습니다.
4. 다음 단계(02~)도 같은 방식입니다. 기본은 A이고, A가 바닥나면 B가 `--takeover`로 이어받습니다.
- `python3 ops.py whoami`나 `bash status.sh download`로 공유 잠금 표(누가 무엇을 어디까지 했는지)를 볼 수 있습니다.

## 실행 방법 (노트북 00)
1. **사람이 한 번:** `pip install modal && modal setup`, `modal secret create huggingface HF_TOKEN=<쓰기 토큰>`
2. `00_setup_and_tests.ipynb`를 위에서부터 실행합니다. 셸에서는 `bash launch.sh setup` 후 `bash status.sh setup`과 같습니다.
3. 결과는 `runs/00_setup/SETUP.md`와 HF `<user>/askos-v1-80m-state/00_setup/`에 남습니다.

에이전트에게 맡길 때는 `00_READ_ME_FIRST_AI_AGENT.md`와 `AGENT_INSTRUCTIONS.md`를 주면 됩니다.

## 상태 (정직하게)
- **00:** Modal에서 통과했습니다. 실측값은 `RESULTS_LOG.md`에 있습니다.
- **01 (코드만, Modal 실행 없음), v1.1 소스:**
  - 소스 12개와 비중은 구현서 그대로입니다. 질이 낮은 하위 소스는 최신 고품질 공개 데이터셋으로 바꿨습니다(NOTES.md D-22 교체표). 바꾼 데이터셋은 Stack-Edu 점수가 붙은 코드, FinePDFs-Edu, 한국어 edu 웹, UltraData-Math L2, FineWiki, Common Pile의 arXiv·오픈 도서·교과서·GitHub 토론·PEP입니다.
  - 필요한 키는 HF 토큰 하나뿐이고, 합성 데이터나 LLM이 다시 쓴 텍스트는 없습니다.
  - 오프라인 시험 `tests/test_download.py` 18개 통과
  - 이 세션에서 실제 데이터로 작게 받아 확인한 것: HF 하위 소스 전부(파일 목록·리비전·필드·본문), Gutenberg, kowikisource, 위키책, ConvoKit, RFC 목록, man-pages, Jargon File
  - **안 돌려 본 것:** StackExchange(7z), 전체 규모 실행. 속도와 시간은 실측 전입니다.
- 구현서에서 벗어난 결정은 `NOTES.md`에 있습니다(D-01~D-22).
