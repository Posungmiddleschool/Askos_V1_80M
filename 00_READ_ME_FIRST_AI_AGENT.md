# 에이전트용 5줄 요약

1. 지금 할 일은 **노트북 00(설정 점검)** 하나입니다. 순서는 `bash launch.sh setup` → 15~20분마다 `bash status.sh setup`입니다.
2. `modal run`을 쓰지 않고, 같은 단계를 두 번 띄우지 않습니다(`CALL_ID.setup`). 토큰을 입력·출력하지 않습니다. 파일을 고치지 않습니다.
3. Modal 시크릿 `huggingface`가 없으면 멈추고 사람에게 `modal secret create huggingface HF_TOKEN=...`을 요청합니다.
4. 끝나면 `runs/00_setup/` 전부, `판정:` 줄, `속도:` 줄을 그대로 인용해 보고하고, 걸린 시간과 비용을 적습니다.
5. 자세한 절차와 문제 대처는 `AGENT_INSTRUCTIONS.md`에 있습니다.
