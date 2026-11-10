# AGENT_INSTRUCTIONS — Askos-V1-80M on Modal

## 목표
Askos-V1-80M(구현서 7판: 커서와 새김)을 Modal에서 노트북 11개(00~10)로 만듭니다. 이 문서는 지금 **노트북 00: 설정과 시험**만 다룹니다. 00은 이후 단계가 쓸 환경을 실제로 점검합니다:
- 패키지, C++ 도구, 설정 일관성, 단위 시험
- Modal Volume 쓰기·읽기 속도
- HF 쓰기 토큰과 비공개 상태 저장소, HF 공개 데이터 읽기 속도
- L4 · L40S · H100 각각에서 bf16 행렬곱 속도, flash-attn varlen 정확도, triton, `torch.compile`, numba, HBM 할당

노트북 01(데이터 다운로드)은 아래 **"노트북 01"** 절에 있습니다. 다른 노트북은 만들어지는 대로 이 문서에 단계가 추가됩니다. 항상 `git pull` 후 지금 맡은 노트북의 절만 따릅니다.

## 파일
| 파일 | 역할 |
|---|---|
| `00_setup_and_tests.ipynb` | 노트북 진입점. `launch.sh`와 `status.sh`만 부릅니다 |
| `modal_app.py` | Modal 이미지와 함수: `setup`(CPU)과 `gpu_probe_l4`, `gpu_probe_l40s`, `gpu_probe_h100` |
| `ops.py`, `launch.sh`, `status.sh` | deploy + spawn 1회(`CALL_ID.<stage>`), 상태 조회, 결과를 `./runs/`에 저장 |
| `setup_check/cpu.py`, `setup_check/gpu.py` | 컨테이너 안에서 실제로 도는 점검 코드 |
| `common/` | 설정 로딩, 토큰 가리기, 로그 형식, 실행 정보 |
| `configs/askos_80m.yaml`, `smoke.yaml`, `infra.yaml` | 구현서 §3, §14 설정과 인프라 설정 |
| `data/sources.yaml` | 구현서 §5.1 소스 매니페스트 |
| `tests/test_infra.py` | GPU 없이 도는 시험 |

## 금지 사항
- `modal run` 금지(셸이 죽으면 작업이 취소됩니다). 항상 `bash launch.sh <stage>`를 씁니다.
- 한 단계는 **한 번만** 띄웁니다. `--again`은 사람이 승인했을 때만 씁니다.
- `askos-v1-80m` 말고 다른 앱은 배포하지 않습니다.
- 토큰을 입력·출력·저장하지 않습니다. 시크릿은 사람이 만듭니다.
- 저장소 파일을 고치지 않습니다. 이상하면 보고합니다.

## 단계
1. **설정 확인 (사람):** `modal setup`, `modal secret create huggingface HF_TOKEN=<쓰기 토큰>`
2. **로컬 시험:** `python3 tests/test_infra.py && python3 -m py_compile modal_app.py ops.py common/*.py setup_check/*.py && bash -n launch.sh status.sh`
3. **실행:** `bash launch.sh setup` → `spawned fc-... -> CALL_ID.setup`이 나오면 성공입니다.
4. **5분 뒤:** `bash status.sh setup` → `상태: RUNNING`. 실시간 로그는 `modal app logs askos-v1-80m`으로 봅니다.
5. **15~20분마다** `bash status.sh setup`을 다시 실행하고, `FINISHED`가 나올 때까지 기다립니다.

### 시간대별로 기대하는 로그
| 시각 | 줄 |
|---|---|
| 0~20분 | 이미지 빌드(첫 실행만): torch, flash-attn 휠, pip |
| 시작 직후 | `실행 정보: seed=1234 config=askos_80m config_hash=... git_commit=... manifest_hash=...` |
| +1분 | `사전 점검 통과: packages`, `toolchain`, `configs`, `unit_tests` |
| +2분 | `속도: Volume 쓰기 ... MB/s, 읽기 ... MB/s (실측, 512 MB)` |
| +3분 | `저장: HF 비공개 저장소 <user>/askos-v1-80m-state 쓰기·읽기 확인`, `속도: HF 공개 데이터 읽기 ... MB/s` |
| GPU마다 | `속도: bf16 행렬곱 ... TFLOPS (실측, ...)`, `사전 점검 통과: flash_attn_varlen`, `사전 점검: HBM .. GB 할당 성공` |
| 끝 | `판정: 00 설정 통과` (또는 `실패: [...]`), `보고서 → .../00_setup/SETUP.md` |

## 끝나면
- `runs/00_setup/`의 파일을 모두 전달합니다: `SETUP.md`, `summary.json`, `*.log.txt`, `*.report.json`
- 다음 줄을 **그대로** 인용합니다: `판정:` 줄 전부, `속도:` 줄 전부, `사전 점검 실패`와 `사전 점검 경고` 줄 전부
- 총 걸린 시간과 Modal 대시보드의 비용을 적습니다.
- 남은 컨테이너가 없는지 확인합니다. 필요하면 `modal app stop askos-v1-80m`을 합니다. 다음 단계에서 다시 deploy합니다.

## 문제 대처표
| 증상 | 조치 |
|---|---|
| `Modal 시크릿 'huggingface'이 없습니다` | 멈추고 사람에게 시크릿 생성을 요청합니다 |
| `hf_auth` 실패, `token_role: read` | 쓰기 권한이 있는 토큰이 필요하다고 보고합니다 |
| `hf_state_repo` 실패 | HF 저장소 생성 권한이나 용량 문제입니다. 오류 줄을 그대로 보고합니다 |
| 이미지 빌드 실패(flash-attn 휠, pip) | 빌드 로그 마지막 40줄을 보고합니다. 파일은 고치지 않습니다 |
| GPU 하나가 `error: ...`(용량 부족, 할당 대기 초과) | 다른 GPU 결과와 함께 그대로 보고합니다. 재시작하지 않습니다 |
| `hbm` 실패(H100에서 75GB 미만) | 보고합니다. 본 학습 설정을 바꿔야 할 수 있습니다(구현서 §16 HBM 부족) |
| `source_hosts` 경고 | 노트북 01에서 그 소스를 받을 수 없다는 뜻입니다. 목록을 보고합니다(필수 아님) |
| `ENDED WITH ERROR` | 재시작하지 말고 `runs/00_setup/error.txt`를 보고합니다 |
| 크레딧 부족 | 우회하지 말고 보고합니다 |

## 요약 (보고서 끝에 붙일 문장)
노트북 00은 Askos-V1-80M을 Modal에서 돌리기 위한 환경을 점검했습니다. CPU · Volume · HF 비공개 저장소 · L4 · L40S · H100에서 각 항목의 통과 여부와 실측 속도를 `runs/00_setup/SETUP.md`에 남겼습니다. 판정이 통과면 노트북 01(데이터 다운로드)로 넘어갑니다. **작업이 끝나면 에이전트에게 준 Modal · HF 토큰을 폐기하세요.**


---

# 노트북 01: 데이터 다운로드 (`01_data_download.ipynb`)

## 목표
`data/sources.yaml`의 소스 12개를 Modal Volume `askos-data`의 `/raw/<소스>/`로 받습니다. 소스마다 목표량은 (read_gb + l3_gb) × 1.6이고 합계 약 99GB입니다. 끝나면 `reports/D1.md`가 Volume과 HF 상태 저장소에 남습니다.

## 추가 파일
| 파일 | 역할 |
|---|---|
| `01_data_download.ipynb` | 노트북 진입점 |
| `data/download.py` | `source <이름>`, `report`, `plan` 명령 |
| `data/quota.py` | 하위 소스별 목표량 배분과 재개(`_state.json`) |
| `data/readers/hf.py`, `data/readers/web.py`, `data/profiles.py`, `data/filters.py` | 소스별 리더와 필터 |
| `data/polite.py` | robots.txt 준수, 호스트당 요청 간격, 재시도, 체크섬 |
| `configs/download.yaml` | 다운로더 설정(필터 기준, 날짜, 요청 한도) |
| `tests/test_download.py` | 네트워크 없이 도는 시험 |

## 금지 사항 (00과 같음, 추가 1개)
- 위 00의 금지 사항을 모두 지킵니다.
- 이 프로젝트가 쓰는 시크릿은 `huggingface` 하나뿐입니다. 다른 키나 서비스 계정을 만들거나 요구하지 않습니다.

## 단계
1. `git pull`
2. 로컬 시험: `python3 tests/test_infra.py && python3 tests/test_download.py`
3. `bash launch.sh download` → `spawned fc-...`가 나오면 성공입니다.
4. 5분 뒤 `bash status.sh download` → `상태: RUNNING`과 소스별 진행률 표가 나옵니다.
5. **20~30분마다** `bash status.sh download`를 다시 실행합니다. 몇 시간이 걸릴 수 있습니다. 진행률 표의 숫자가 늘고 있으면 정상입니다.
6. `FINISHED`가 나오면 `runs/01_download/D1.md`를 확인합니다.

### 시간대별로 기대하는 로그 (`modal app logs askos-v1-80m`)
| 시각 | 줄 |
|---|---|
| 0~15분 | 이미지 재빌드(apt: p7zip, rsync, groff). 이미지가 바뀌었으니 첫 실행 한 번만 |
| 시작 | `[download] 12개 소스 병렬 시작: [...]` |
| 소스마다 | `실행 정보: ...`, `다운로드 시작: <소스> 목표 X GB`, `할당 1차: ...` |
| 진행 중 | `저장: <하위 소스> 유닛 i/N · 누적 X GB · 문서 N` (256MB마다) |
| 하위 소스 끝 | `소진: <하위 소스> ...` 또는 `실패: <하위 소스> ...` |
| 소스 끝 | `속도: <소스> 텍스트 X MB/s (실측 ...)`, `판정: <소스> 목표 도달/소스 소진/미달` |
| 전체 끝 | `판정: 01 다운로드 통과/미완`, `보고서 → .../D1.md` |

## 끝나면
- `runs/01_download/`의 파일을 모두 전달합니다: `D1.md`, `summary.json`, `*.log.txt`, `*.report.json`
- 그대로 인용할 줄: `판정:` 줄 전부, `속도:` 줄 전부, `실패:` 줄 전부
- 총 시간과 Modal 비용(대시보드)을 적습니다.

## 문제 대처표
| 증상 | 조치 |
|---|---|
| `[download] 끝: <소스> rc=-1 ...` (컨테이너가 죽음) | 보고합니다. 사람이 승인하면 `bash launch.sh download --again`. 받은 부분은 이어집니다 |
| `실패: <하위 소스> ... HTTPError 429/503` | 사이트 일시 제한입니다. 보고합니다. 다음 `--again`에서 자동으로 최대 3번까지 다시 시도합니다 |
| `RobotsDisallowed` | 그 사이트의 robots.txt가 막은 것입니다. **우회하지 않고** 보고합니다 |
| `md5 mismatch` (StackExchange) | archive.org 파일 손상이나 변경입니다. 보고합니다 |
| `verify failed: ...` | 데이터셋 필드나 구조가 바뀐 것입니다. 파일을 고치지 말고 오류 줄 그대로 보고합니다 |
| 소스 판정 `소스 소진(작은 소스)` | 정상일 수 있습니다(예: hacker_docs, rebel_classics). 부족분은 노트북 02의 분할 규칙이 FineWeb-Edu로 채웁니다 |
| 하위 소스가 몇 MB에서 `소진` (libretexts, pressbooks, oercommons, PEP, kowikibooks) | 정상입니다. 원래 작은 데이터셋이고, 남은 몫은 같은 소스의 큰 하위 소스가 채웁니다 |
| Volume 용량 부족 | 보고합니다(raw는 압축해서 약 30~40GB 예상) |
| `ENDED WITH ERROR` | 재시작하지 말고 `runs/01_download/error.txt`를 보고합니다 |

## 요약 (보고서 끝에 붙일 문장)
노트북 01은 Askos-V1-80M의 사전학습 데이터 소스 12개를 Modal Volume에 받았습니다. 소스별 실제 바이트, 리비전, 체크섬, verify 결과는 `reports/D1.md`에 있습니다. 판정이 통과면 노트북 02(정제, 중복 제거, 분할)로 넘어갑니다. **작업이 끝나면 에이전트에게 준 Modal · HF 토큰을 폐기하세요.**
