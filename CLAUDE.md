# 작업 규칙 (Claude용)

- 모든 커밋은 `main`에 바로 올립니다(`git push origin main`). 다른 브랜치를 만들지 않습니다.
- 노트북마다 ZIP은 만들지 않습니다. 에이전트는 이 저장소를 클론하거나 `git pull`해서 씁니다.
- 실행은 사용자의 다른 에이전트가 Modal에서 합니다. Claude는 modal을 설치하거나 실행하지 않고 코드만 씁니다.
