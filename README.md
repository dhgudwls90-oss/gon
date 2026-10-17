# DART 공시 텔레그램 알림 봇

삼성전자 · SK하이닉스 · 리브스메드의 DART 새 공시를 텔레그램으로 알려줍니다.
GitHub Actions가 한국시간 07~21시에는 5분마다, 그 외 시간에는 30분마다 확인합니다.

- 필요한 Secrets: `DART_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- 감시 회사 변경: `.github/workflows/dart-bot.yml`의 `COMPANIES` 값 수정
- 수동 실행: Actions 탭 → "DART 공시 알림" → Run workflow
