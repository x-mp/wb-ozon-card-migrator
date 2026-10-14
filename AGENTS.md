# Правила агентов мигратора

- Не хранить и не выводить API-ключи, cookies, приватные ключи и данные кабинетов.
- Читать Wildberries только через официальный API и загружать в Ozon только
  утверждённый неизменяемый план.
- Перед повторной диагностикой искать похожий опыт в `marketplace-memory`.
- После проверенного результата сохранять краткую запись со ссылками на
  `run_id`, `plan_id` и commit; аудит операции остаётся в `operations/migrations`.
- Не сохранять в память полный ответ API, traceback с локальными переменными или чат.
- Поддерживать `CHANGELOG.md`, `docs/architecture.md`, `RECOVERY.md` и
  `COMMIT_POLICY.md`. Commit обязан содержать конкретный subject и body с
  `Why`, `What`, `Verification`, `State`; бессодержательные сообщения запрещены.
- Для каждого существенного изменения добавлять датированную запись
  `docs/change-journal/YYYY-MM-DD-short-slug.md` с `Date`, `Why`, `What`,
  `How`, `Verification`, `State`. Journal хранит логику и доказательства,
  `CHANGELOG.md` — сводную историю; секреты, полный чат и tool transcript
  запрещены.
