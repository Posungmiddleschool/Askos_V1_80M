# NOTES — 구현서 7판에서 벗어난 결정과 근거

| ID | 결정 | 근거 |
|---|---|---|
| D-01 | 다시 만들 수 있는 데이터(raw, clean, read/l3/val 바이너리, L3 색인, 앵커 189GB)는 **Modal Volume `askos-data`**에 둡니다. 상태(체크포인트, 로그, 보고서)는 **HF 비공개 저장소 `<user>/askos-v1-80m-state`**에만 둡니다 | Modal 가이드는 Volume을 쓰지 말라고 하지만, 약 250GB를 학습 구간마다 HF에서 다시 받는 것은 비싸고 HF 비공개 용량 한도에도 걸립니다. Volume에 있는 것은 모두 노트북 01~03으로 다시 만들 수 있습니다 |
| D-02 | GPU: 본 학습·사전 점검·평가(08~10)는 **H100 80GB**, 새김(05)은 L40S, 커서 커널·스모크·참조 모델(04, 06, 07)은 L4를 씁니다 | 본 학습은 HBM이 75GB 이하로 필요해 80GB급만 됩니다. 나머지 단계는 작은 GPU로 충분합니다. 구현서의 "A100 24시간"은 **24 GPU시간 예산**으로 읽고, H100에서 15,000스텝이 몇 시간에 끝나는지는 노트북 08에서 실측해 정합니다 |
| D-03 | `train.checkpoint_every_hours`를 2에서 **1**로 바꿉니다 | 가이드는 30분을 권하지만 전체 상태(새김 표 포함)가 약 18GB입니다. Modal 선점이나 중단으로 잃는 시간과 업로드 비용 사이의 절충입니다 |
| D-04 | 벤치마크(GSM8K, MMLU, GPQA, ARC, MATH-500, AIME)와 겹치는 문서를 노트북 02에서 뺍니다 | 가이드 규칙 7. 구현서에는 없습니다 |
| D-05 | 버전 고정: Python 3.11, torch 2.6.0(cu124, triton 3.2.0 포함), flash-attn 2.7.4.post1(미리 빌드된 휠), huggingface_hub 0.33.4 + hf-xet 1.1.5(명시 설치. 0.30.2의 `[hf_xet]` extra는 설치되지 않았음: 00 첫 실행), CUDA 12.4 devel 이미지 | 구현서 §1(Py 3.11, PyTorch 2.5+, flash-attn 2.x, CUDA 12.x)을 만족하면서 미리 빌드된 flash-attn 휠이 있는 조합입니다 |
| D-06 | 본 학습(09)은 6시간 구간으로 나눕니다. 각 구간이 끝나면 Hub에 저장하고 다음 구간을 spawn합니다 | Modal 함수 하나의 타임아웃은 최대 24시간이고, 예산 24시간에 재개 시간을 더하면 이를 넘습니다. 사람은 한 번만 띄웁니다 |
| D-07 | 저장소 루트가 구현서 §4의 `askos/`입니다. 여기에 `common/`, `setup_check/`, `modal_app.py`, `ops.py`를 더합니다 | 가이드의 "train.py 한 파일" 대신 구현서의 패키지 구조를 따르고, 노트북을 얇게 두는 원칙만 지킵니다 |
| D-08 | 인프라 이름(Modal 앱, Volume, 시크릿, 상태 저장소, 단계별 GPU)은 `configs/infra.yaml`에 둡니다 | 규칙 1(숫자는 설정에만). 모델 설정(`askos_80m.yaml`)은 구현서와 같게 유지합니다(D-03 제외) |
| D-09 | raw 문서에 `text, source, doc_id` 외에 작은 `meta` 사전을 둡니다. 예: `int_score`, `se_score`, `hn_score`, `code_score`, `url` | 구현서 §5.5-3의 감쇠 스트림이 FineWeb-Edu 5점, 고득점 StackExchange, HN 고득점, 교육 점수 상위 코드를 골라야 해서 필요합니다 |
| D-10 | code 소스의 "상위 절반"은 구현서가 허용한 **대체 휴리스틱**(주석 비율, 줄 길이: `data/filters.py:code_quality`)으로 고릅니다. 기준값은 처음 2개 파일의 중앙값으로 정해 고정합니다 | Stack-Edu 분류기는 언어마다 GPU 분류기가 따로 필요해 v1에서는 쓰지 않습니다. v2 후보입니다 |
| D-11 | 한 소스 안의 하위 소스 몫: 1차에 남은 목표를 활성 하위 소스에 똑같이 나누고, 소진된 하위 소스가 못 채운 몫은 다음 차례에 나머지가 나눠 채웁니다 | 구현서에 하위 소스별 비율이 없습니다. 가장 단순한 해석입니다 |
| D-12 | HF 데이터는 `load_dataset(streaming=True)` 대신, 데이터셋 카드의 `configs → data_files`(또는 `configs/download.yaml`의 `hf.files`)로 고른 train 파일을 **한 커밋에 고정**해 직접 읽습니다 | 문서는 같습니다. 대신 (파일, 행) 단위로 정확히 이어 받을 수 있고, hf-xet으로 병렬로 받을 수 있습니다. 리비전은 D1.md에 기록합니다 |
| D-13 | hacker_docs: RFC는 rsync로 받고(실패하면 rfc-index와 HTTP로 한 편씩), man-pages는 최신 upstream tarball을 groff로 렌더링합니다. "Debian manpages"는 upstream과 겹쳐 받지 않습니다. Jargon File은 4.4.7 텍스트판을 씁니다 | rfc-editor.org의 bulk tar 주소가 사라졌습니다(404, 2026-11-10 확인) |
| D-14 | **LKML(lore.kernel.org)은 받지 않습니다** | robots.txt가 `User-agent: *`에 `Disallow: /`를 걸었고 AI 수집기 이름도 따로 막았습니다(2026-11-10 확인). 구현서 §5.2(robots 준수)를 따릅니다. geek_debate의 남은 몫은 lists.debian.org, irclogs, ConvoKit, HN이 나눠 채우고, 그래도 모자라면 §5.5-2 작은 소스 규칙(fineweb_edu로 채움)이 적용됩니다 |
| D-15 | **HN은 BigQuery 대신 공개 HF 아카이브 `open-index/hacker-news`(odc-by, 2006~현재, 월별 parquet)에서 받습니다.** 점수 100 이상인 스토리마다, 그 달과 다음 2달의 댓글을 트리(답글 깊이만큼 들여쓰기)로 붙여 스레드 문서 하나를 만듭니다. 최신 달부터 거꾸로 받고, 댓글 기간이 아직 다 차지 않은 마지막 3달은 뺍니다 | 구상자 결정(2026-11-10): "HF 토큰 말고 키나 서비스 계정이 필요한 소스는 모두 빼고, 고품질 공개 데이터셋을 쓴다". 구현서 §1-5(§5의 소스만)의 예외이며, 필터(score ≥ 100)는 구현서와 같습니다. 2020-01 실측: 스레드 1,296개, 69MB |
| D-16 | Gutenberg: 구현서의 성(surname)을 카탈로그 저자 접두사로 바꿉니다(`configs/download.yaml`). Illustrator, Translator, Editor 역할은 제외합니다. 받을 때 PG 머리말과 꼬리말(라이선스 문구)을 잘라 냅니다 | "Swift", "Wells" 같은 성만으로는 다른 저자가 섞입니다. 같은 라이선스 문구가 책 수백 권에 반복되는 것도 막습니다 |
| D-17 | peS2o는 v2를 씁니다 | 저장소 README가 "peS2o V2 (Latest)"라고 합니다. v3는 문서화돼 있지 않습니다 |
| D-18 | StackExchange: 질문 하나당 문서 하나로 만들고, 점수 5 이상 답변을 모두 점수 순으로 붙입니다 | 답변마다 문서를 만들면 질문이 반복돼 중복 제거(§5.4)와 충돌합니다 |
| D-19 | DCLM "상위 10%": 처음 4개 파일의 fastText 점수 90% 분위수를 기준값으로 고정합니다 | 전체 분포는 4T 토큰을 다 읽어야 알 수 있습니다. DCLM 샤드는 무작위 분할이라 앞 파일로 추정해도 됩니다 |
| D-20 | irclogs는 2025-10-31부터 거꾸로 하루씩 받고 입장·퇴장·닉 변경 줄을 뺍니다. lists.debian.org는 요청 6,000회까지만 보냅니다(초당 1회) | 재현성(고정 날짜)과 사이트 부담 제한 때문입니다 |
| D-21 | OpenStax: CC BY 책은 `retired` 상태도 포함합니다 | 2026-11-10 기준 CC BY 38권 가운데 앞쪽 표본이 모두 `retired`였습니다(새 판은 NC-SA로 바뀐 경우가 많음). 옛 판도 CC BY 그대로이고, 판 사이의 근사 중복은 §5.4가 처리합니다 |
