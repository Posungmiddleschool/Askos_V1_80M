# WORKLOG — Askos-V1-80M

이 프로젝트에서 **무엇을 했고, 무엇을 안 했고, 무엇이 남았는지** 적는 작업 일지입니다.
새 세션의 Claude, 사람, 실행 에이전트는 **이 파일부터** 읽습니다. 코드를 바꿀 때마다 이 파일도 함께 고칩니다(CLAUDE.md 규칙).

- 마지막 갱신: 2026-11-11 (순차 이어받기 반영)
- 저장소: `Posungmiddleschool/Askos_V1_80M`, 브랜치 `main`
- 기준 문서: 구현서 `ASKOS_PRETRAIN_v7.md`(커서와 새김), `MODAL_NOTEBOOK_GUIDE.md`. 둘 다 사용자가 대화에 첨부한 파일이고, **저장소에는 없습니다**(아래 "열린 문제" 1)

---

## 1. 한눈에 보는 현재 상태

| # | 노트북 | 코드 | Modal 실행 | 결과 |
|---|---|---|---|---|
| 00 | `00_setup_and_tests.ipynb` | 완료 | **통과** (계정 A, 커밋 `5e4fc3e`, 2026-11-10) | `RESULTS_LOG.md` 1줄. 계정 B는 실행 기록 없음 |
| 01 | `01_data_download.ipynb` | 완료 (데이터 v1.1 + 여러 계정 지원) | **아직 안 함** | — |
| 02 | 정제·중복 제거·벤치마크 제거·분할 | 안 만듦 | — | — |
| 03 | L3 색인·앵커·문서 해시·로더 | 안 만듦 | — | — |
| 04 | 커서 커널 (numba·triton, T14~T20) | 안 만듦 | — | — |
| 05 | 새김 (T4·T5·T6·T21) | 안 만듦 | — | — |
| 06 | 모델 + 스모크 | 안 만듦 | — | — |
| 07 | 참조 모델 | 안 만듦 | — | — |
| 08 | 본 크기 사전 점검 | 안 만듦 | — | — |
| 09 | 본 사전학습 (24 GPU시간, H100) | 안 만듦 | — | — |
| 10 | 평가·보고서 | 안 만듦 | — | — |

**다음 할 일:** 계정 A로 노트북 01을 실행합니다(프롬프트는 6.1절). A의 크레딧이 떨어지면 계정 B가 이어받습니다(6.2절). 그 결과(D1.md)를 보고 노트북 02를 만듭니다.

---

## 2. 사용자가 정한 것 (바꾸지 않음)

| 날짜 | 결정 |
|---|---|
| 11-09 | 모든 실행은 Modal에서 합니다. A100이 아니어도 됩니다. 실행은 **사용자의 다른 AI 에이전트**가 하고, Claude는 **코드만** 씁니다(modal을 설치하거나 실행하지 않음) |
| 11-09 | Claude의 권장안을 모두 채택합니다(아래 D-01~D-08) |
| 11-10 | 모든 커밋은 `main`에 바로 올립니다. 다른 브랜치를 만들지 않습니다 |
| 11-10 | ZIP을 만들지 않습니다. 에이전트가 저장소를 클론하거나 pull해서 씁니다 |
| 11-10 | 목표는 "구현서의 1000배"입니다. 점진 개선이 아닌 그대로의 꿈을 실행하고, v1 코드를 최선을 다해 짭니다 |
| 11-10 | HF 토큰 말고 다른 키나 서비스 계정이 필요한 소스는 뺍니다(BigQuery HN 제거) |
| 11-10 | 질이 낮다고 판단한 소스는 최신 고품질 공개 데이터셋으로 모두 바꿉니다(데이터 v1.1) |
| 11-11 | Modal 계정을 2개 씁니다(사용자와 친구, Volume은 각자). 해법 2: **공유 기준은 HF, 각 Volume은 캐시** |
| 11-11 | **계정 A가 전부 하고, A의 무료 크레딧이 떨어지면 계정 B가 이어받습니다**(나눠서 동시에 하지 않음). B는 사람 승인 뒤 `--takeover` |
| 11-11 | HF 조직은 쓰지 않습니다. **HF 계정 하나(`AskosAILabs`)**를 두 Modal 계정이 함께 씁니다 |

---

## 3. 커밋별로 한 일

| 커밋 | 날짜 | 내용 |
|---|---|---|
| `66e6af7` | 11-09 | 노트북 00과 공통 틀: `modal_app.py`(이미지·래퍼·setup·GPU 점검 3종), `ops.py`·`launch.sh`·`status.sh`, `setup_check/`, `common/`, 구현서 설정(§3, §14, §5.1), `tests/test_infra.py`, 지침서, NOTES D-01~D-08 |
| `ef19d80` | 11-10 | 00의 1차 실패를 고침: `hf_xet` 미설치(huggingface_hub 0.33.4 + hf-xet 1.1.5로 명시 설치), catb.org는 http로, SETUP.md에 실패 원인 줄을 추가 |
| `13e0084` | 11-10 | CLAUDE.md 추가(main에 커밋, ZIP 없음, 코드만) |
| `5e4fc3e` | 11-10 | 00의 2차 실패를 고침: 점검 코드의 import 위치 버그(`is_xet_available`). 환경은 정상이었음. **이 커밋으로 00 통과** |
| `5056de5` | 11-10 | 노트북 01: 12개 소스 다운로드, 목표량 배분과 재개, HF·웹 리더, robots.txt 준수, D1 보고서. NOTES D-09~D-21 |
| `5eb41c1` | 11-10 | HN을 BigQuery에서 공개 HF 아카이브 `open-index/hacker-news`로 바꾸고 GCP를 완전히 제거 |
| `78f6b59` | 11-10 | 데이터 v1.1: 하위 소스를 고품질 데이터셋으로 교체(4절 표, NOTES D-22) |
| `0febcda` | 11-11 | 여러 Modal 계정 지원: `common/hub.py`(HF 잠금, 하트비트, 넘겨받기, push/pull), `hub_status`, `ops.py --sources/--redo-done/whoami`, 00은 계정별로, NOTES D-23 |
| `0e6396b` | 11-11 | 공유 네임스페이스를 HF 계정 `AskosAILabs` 하나로 정함(조직 안 씀) |
| `4553140` | 11-11 | WORKLOG.md 추가, CLAUDE.md에 "WORKLOG 먼저 읽고 함께 고치기" 규칙 |
| (이번 커밋) | 11-11 | 순차 이어받기: `--takeover`(A가 실행 중으로 남긴 잠금을 바로 넘겨받음), 넘겨받힌 컨테이너는 하위 프로세스를 멈추고 업로드하지 않음, 하트비트 10분, HF 업로드 20분 간격, 문서 전부 A→B 순서로 고침 |

---

## 4. 데이터 (노트북 01) 현재 구성

소스 12개의 이름, 비중, read_gb, l3_gb는 구현서 §5.1과 같습니다. 목표 합계는 99.2GB(= (read_gb + l3_gb) × 1.6)입니다. 하위 소스는 v1.1 기준입니다.

| 소스 | 목표 GB | 하위 소스 (v1.1) | 필터 |
|---|---|---|---|
| fineweb_edu | 23.57 | HuggingFaceFW/fineweb-edu sample-100BT | int_score ≥ 4 (실측 13.6%) |
| dclm_top | 11.79 | mlfoundations/dclm-baseline-1.0 | fastText 점수 상위 10% (앞 4개 파일로 기준값, 1개 파일 시험에서 0.525) |
| code | 11.79 | common-pile/stackv2_edu_filtered | Stack-Edu 점수 상위 절반 (파일을 시드 1234로 섞고, 앞 6개 파일로 기준값. 2개 파일 시험에서 2.47) |
| korean_web | 11.79 | finepdfs-edu kor_Hang, eliceai/korean-webtext-edu, fineweb-2 kor_Hang | score ≥ 3, language_score ≥ 0.9 |
| papers_books | 10.61 | peS2o v2, common-pile arxiv_papers / doab / libretexts / pressbooks / oercommons (filtered), finepdfs-edu eng_Latn, en·ko 위키책 덤프 | — |
| math | 9.42 | finemath-4plus, UltraData-Math L2-preview, proof-pile-2 algebraic-stack | — |
| stackexchange | 4.10 | archive.org stackexchange, 9개 사이트 | 답변 점수 ≥ 5. 질문 하나에 답변을 모아 문서 하나 |
| geek_debate | 3.07 | open-index/hacker-news, common-pile github_archive / PEPs, ConvoKit CMV | HN 스토리 점수 ≥ 100. 스토리 달과 다음 2달의 댓글을 트리로 붙임 |
| namuwiki | 3.07 | heegyu/namuwiki-extracted | — (연구용) |
| wikipedia | 5.89 | HuggingFaceFW/finewiki ko, en | — |
| hacker_docs | 2.05 | RFC (rsync, 막히면 HTTP), man-pages 6.20 (groff로 렌더링), Jargon File 4.4.7 | — |
| rebel_classics | 2.05 | Gutenberg 13명 (823권 + Abbott), kowikisource 이상·채만식·김유정 | PG 머리말·꼬리말 제거 |

**뺀 것과 이유:**
- github-code-clean과 휴리스틱 필터: 구현서의 대체안일 뿐이라 Stack-Edu 점수로 교체
- open-web-math: FineMath보다 품질이 낮음
- RedPajama arXiv: 외부 서버, 라이선스 혼재
- OpenStax: PDF를 pdftotext로 바꾸면 머리글·쪽 번호가 섞임
- wikimedia/wikipedia 20231101: 오래된 덤프
- irclogs: 짧은 채팅과 빈 파일
- lists.debian.org: 양이 적음
- **LKML: robots.txt가 모든 수집을 금지(AI 수집기도 이름으로 금지)**
- BigQuery HN: GCP 키가 필요

**검토했지만 안 쓴 것:**
- Nemotron-CC-Math: LLM이 본문을 다시 씀
- UltraData L3: 합성 데이터
- FineWeb2-HQ: 한국어 없음
- Stack-Edu 원본: 본문이 S3에 있어 AWS 키가 필요

---

## 5. 검증 상태 (정직하게)

### 5.1 Modal에서 실제로 실행한 것
- 노트북 00만, 계정 A에서 3번 실행했고 3번째에 통과했습니다.
  - H100 bf16 804 TFLOPS, L40S 210, L4 59
  - flash-attn varlen 오차 ≤ 0.010
  - H100에서 HBM 75GB 할당 성공
  - Volume 쓰기 239MB/s · 읽기 1,176MB/s, HF 공개 데이터 읽기 108MB/s
  - RAM 426GB, CPU 20개 보임

### 5.2 이 세션(Claude 쪽)에서 Modal 없이 확인한 것
- **오프라인 시험:**
  - `tests/test_infra.py` 10개
  - `tests/test_download.py` 18개
  - `tests/test_hub.py` 7개
  - 모두 통과
- **실제 데이터로 작게 받아 본 것:**
  - DCLM 30MB, Stack-Edu 4MB, GitHub archive·PEP·UltraData L2·FineWiki ko·LibreTexts·OER·arXiv(CP)·korean-webtext-edu·FinePDFs-Edu kor 각 4MB, FineMath·Proof-Pile 각 6MB
  - HN 2020-01분(스레드 1,296개, 69MB)
  - kowikisource(211편, 5.5MB), 위키책 ko, Gutenberg 카탈로그, ConvoKit, Jargon File, man-pages 목록, RFC 목록 8,873편
  - 나머지 HF 소스는 파일 목록과 리비전 확인
- `huggingface_hub` 0.33.4의 실제 함수 시그니처와 `common/hub.py` 호출을 대조했습니다.

### 5.3 아직 한 번도 안 돌려 본 것
- 노트북 01 전체 실행. 속도, 시간, 비용 모두 추정뿐입니다(전체 2~4시간, 약 $5~15).
- StackExchange 7z 압축 풀기(이 세션에 7z가 없음)
- RFC rsync 모듈 이름 `rfcs-text-only`(틀리면 HTTP로 넘어가는데 약 2.5시간)
- **HF 잠금과 push/pull을 실제 HF에서 쓰는 것.** 이 세션에는 HF 토큰이 없습니다. 부모 커밋이 맞지 않을 때 HF가 주는 응답 코드는 409나 412로 가정했고, 메시지에 "parent"가 있는 경우도 처리합니다.
- A에서 B로 실제로 이어받는 흐름(`--takeover`, HF pull, 넘겨받힌 쪽 컨테이너가 스스로 멈추는 것)
- 노트북 00의 새 점검 항목(`hf_shared_repos`, `shared_namespace`). 00이 통과한 커밋(`5e4fc3e`)보다 뒤에 추가됐습니다.

---

## 6. 지금 에이전트에게 보낼 프롬프트 (노트북 01)

**순서:** 계정 A가 전부 합니다. A의 무료 크레딧이 떨어지면, 사람이 확인한 뒤 계정 B가 이어받습니다(NOTES.md D-23).

### 6.1 계정 A의 에이전트에게 (지금 보낼 것)
```
Askos-V1-80M 프로젝트의 노트북 01(데이터 다운로드)을 Modal에서 실행해 줘. 노트북 00은 이미 통과했어.

1. 저장소: 처음이면 git clone https://github.com/Posungmiddleschool/Askos_V1_80M.git && cd Askos_V1_80M,
   이미 있으면 cd Askos_V1_80M && git pull.
   WORKLOG.md, 00_READ_ME_FIRST_AI_AGENT.md, AGENT_INSTRUCTIONS.md의 "노트북 01" 절을 먼저 읽고 금지 사항을 지켜.
2. pip install -q "modal>=1.0" pyyaml numpy
   python3 tests/test_infra.py && python3 tests/test_download.py && python3 tests/test_hub.py
   python3 ops.py whoami → Modal 계정 이름과 공유 HF 네임스페이스(AskosAILabs여야 함)를 알려 줘.
   Modal 시크릿 huggingface가 없으면 멈추고 나에게 말해. 토큰은 내가 직접 넣어.
3. bash launch.sh download   (소스 12개 전부)
   출력된 공유 잠금 표와 "이 계정이 맡을 소스" 줄을 보여 줘.
4. 20~30분마다 bash status.sh download로 진행률을 확인해. 2~4시간쯤 걸릴 수 있어.
5. 끝나면 runs/01_download/의 D1.md와 summary.json, 로그의 "판정:", "속도:", "실패:" 줄을 그대로 인용하고,
   총 시간과 Modal 비용도 알려 줘.

크레딧이 떨어지거나 결제 오류가 나면 다시 시도하지 말고, 그 오류 줄과 bash status.sh download 출력을 그대로 보고하고 멈춰.
지킬 것: modal run 금지. --again·--redo-done·--takeover는 내 승인 없이 쓰지 마. 공유 잠금을 지우거나 우회하지 마.
파일을 고치지 마. 토큰을 입력하거나 출력하지 마. ENDED WITH ERROR가 나오면 재시작하지 말고 error.txt를 그대로 보고해.
```

### 6.2 계정 B의 에이전트에게 (A의 크레딧이 떨어진 것을 사람이 확인한 뒤에만)
```
Askos-V1-80M 프로젝트의 노트북 01을, 크레딧이 떨어져 멈춘 계정 A로부터 이어받아 Modal에서 실행해 줘.

1. git clone https://github.com/Posungmiddleschool/Askos_V1_80M.git && cd Askos_V1_80M (있으면 git pull).
   WORKLOG.md, 00_READ_ME_FIRST_AI_AGENT.md, AGENT_INSTRUCTIONS.md의 "노트북 01" 절을 먼저 읽어.
2. pip install -q "modal>=1.0" pyyaml numpy, 시험 3개(test_infra, test_download, test_hub)를 돌려.
   python3 ops.py whoami → Modal 계정 이름(계정 A와 달라야 함)과 공유 네임스페이스(AskosAILabs)를 알려 줘.
   이 계정에서 노트북 00을 한 번도 안 돌렸다면 먼저 bash launch.sh setup → bash status.sh setup으로 통과를 확인해.
3. bash launch.sh download --takeover
   잠금 표, "이어받기" 줄, "이 계정이 맡을 소스" 줄을 보여 줘. A가 끝낸 소스는 자동으로 빠져.
4~5. 계정 A용과 같아: 20~30분마다 bash status.sh download, 끝나면 D1.md·summary.json·"판정:"/"속도:"/"실패:" 줄·시간·비용 보고.

지킬 것: modal run 금지. --takeover는 이번 한 번만. --again·--redo-done은 내 승인 없이 쓰지 마.
잠금을 지우거나 우회하지 마. 파일을 고치지 마. 토큰을 입력하거나 출력하지 마.
```

---

## 7. 열린 문제와 사람이 할 일

1. **구현서와 가이드 원문이 저장소에 없습니다.** 새 Claude 세션은 첨부 없이는 구현서를 읽을 수 없습니다. 저장소에 넣을지(예: `docs/ASKOS_PRETRAIN_v7.md`) 정해 주세요. 노트북 02 이후는 구현서 §5.3~§16을 그대로 따라야 합니다.
2. **기본 브랜치:** GitHub 기본 브랜치가 아직 `ccr-cab7f42f-5h45f7`일 수 있습니다. Settings → General → Default branch에서 `main`으로 바꾸세요. 바꾸면 그 브랜치를 지워도 됩니다(내용은 `main`에 모두 있음).
3. **토큰:**
   - `AskosAILabs` HF 계정에서 사람마다 따로 쓰기 토큰을 만들어, 각자 Modal 시크릿 `huggingface`에 넣습니다.
   - 두 사람의 Modal 계정 이름(`ops.py whoami`)이 같으면 한쪽은 `ASKOS_ACCOUNT`를 지정합니다. 이름이 같으면 B의 `--takeover`가 A의 잠금을 "자기 것"으로 착각합니다.
4. **HF 용량:** 무료 계정은 비공개 100GB입니다. raw(압축 약 35GB)와 다음 단계 결과물이 쌓이면 모자랄 수 있습니다. 02 단계가 끝나면 raw를 지우거나 PRO로 올립니다.
5. **korean-webtext-edu:** 원본(HAERAE-HUB/KOREAN-WEBTEXT)의 라이선스가 적혀 있지 않습니다. 연구용이면 문제없지만, 상업적으로 쓰려면 빼야 합니다.
6. **나무위키**(2021, 비영리 라이선스, 품질 중간)는 구현서대로 남겨 두었습니다. 빼거나 바꿀지는 사용자가 정합니다.
7. **1000배 주장:** 구현서 정리 0.3은 공리 A1~A6이 맞을 때 성립하는 조건부 주장입니다. 학습 로그에 A4·A5·A6이 찍히도록 만들 예정입니다. 배수 K 자체를 보이려면 표준 경사하강 기준선과 비교해야 해서, 노트북 06이나 08에 비교 실험을 넣을지 정해야 합니다.

---

## 8. 노트북 02 이후에 반드시 할 일 (미리 적어 둠)

- **02:** 구현서 §5.3 정제, §5.4 중복 제거, §5.5 분할, T12. 추가로 벤치마크와 겹치는 문서 제거(NOTES D-04). 입력은 공유 HF의 `raw/`이고, 계정마다 `hub.pull_dir`로 받습니다. 출력(read/l3/val 바이너리)은 HF에 올립니다. 정제·중복 제거 작업 단위마다 잠금을 둡니다.
- **03:** 앵커 189GB는 HF에 올리지 않고 쓰는 계정이 다시 만듭니다(NOTES D-23).
- **09:** 6시간 구간으로 나누고, 구간이 끝나면 스스로 다음 구간을 spawn합니다(D-06). 체크포인트는 1시간마다 HF에 올립니다(D-03).
- 모든 단계: 시작할 때 실행 정보(시드, 설정 해시, git, 매니페스트 해시)를 기록하고, 숫자는 실측만 쓰며, 끝나면 `RESULTS_LOG.md`에 한 줄을 추가합니다.

---

## 9. 파일 지도

| 경로 | 내용 |
|---|---|
| `modal_app.py` | Modal 이미지와 함수: setup, gpu_probe_*, download, download_source, hub_status |
| `ops.py`, `launch.sh`, `status.sh` | 에이전트가 쓰는 실행·상태 명령(+ `python3 ops.py whoami`) |
| `common/` | config(설정·해시), redact(토큰 가리기), log(실행 정보), validate, hub(공유 HF 잠금·동기화) |
| `setup_check/` | 노트북 00의 CPU·GPU 점검 |
| `data/` | download, quota(배분·재개), io, polite(robots·체크섬), profiles, filters, readers/(hf, hn, web), sources.yaml |
| `configs/` | askos_80m.yaml(구현서 §3, 체크포인트 간격만 1시간), smoke.yaml(§14), download.yaml, infra.yaml |
| `tests/` | test_infra, test_download, test_hub |
| `NOTES.md` | 구현서에서 벗어난 결정 D-01~D-23과 근거 |
| `RESULTS_LOG.md` | 실행 결과(실측) 한 줄씩 |
| `AGENT_INSTRUCTIONS.md`, `00_READ_ME_FIRST_AI_AGENT.md` | 실행 에이전트용 지침 |
