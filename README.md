# Миграция карточек Wildberries → Ozon

Проект входит в общую Marketplace Platform. Он использует единый внешний
`MARKETPLACE_DATA_ROOT`, читает raw/normalized через контракты
`marketplace-data-skills` и записывает планы, подтверждения и результаты только
в `operations/migrations/`. Данные и ключи не хранятся в Git.

Безопасный набор Python-скриптов для переноса принадлежащей продавцу карточки
товара из кабинета Wildberries в собственный кабинет Ozon через официальные
API маркетплейсов.

Проект получает и архивирует исходную карточку WB, загружает актуальную схему
категории Ozon, адаптирует поля и изображения, формирует план предварительной
проверки, требует отдельного подтверждения, отправляет одну карточку и
проверяет асинхронную задачу Ozon. Секреты, ответы кабинетов, реальные карточки
и изображения в репозиторий не входят.

## Как обеспечивается безопасность

- Доступ к WB используется только для чтения.
- Чтение Ozon и запись в Ozon разделены на разные команды.
- Cookies кабинетов и автоматизация браузера не используются.
- Каждый ответ API сохраняется как новый неизменяемый запуск с источником,
  временем запроса, HTTP-статусом и контрольной суммой.
- Загрузка заблокирована, пока план не пройдёт проверку, его хеш не будет
  отдельно подтверждён и не будет передана точная строка подтверждения.
- Первый пилот отправляет только один товар, не меняет остатки и ничего не
  удаляет.

Дополнительные документы:

- [архитектура и разделение операций](docs/architecture.md);
- [получение и настройка ключей](docs/credentials.md);
- [бренд, НДС, страна производства и ТН ВЭД](docs/product-fields.md);
- [правила работы с секретами](SECURITY.md).

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[media]"
cp .env.example .env
chmod 600 .env
```

Заполните локальный `.env`: добавьте токен WB с правом чтения контента и пару
Seller `Client ID + Api-Key` из собственного кабинета Ozon.

## 1. Получить карточку из WB

```bash
PYTHONPATH=src python scripts/wb_cards.py \
  --nm-id WB_NM_ID \
  --account main \
  --output data/operations/migrations/wb-card.json
```

Сборщик использует официальный метод
`POST /content/v2/get/cards/list`, безопасно обрабатывает пагинацию и сохраняет
каждую исходную страницу ответа. Существующий выходной файл не
перезаписывается.

## 2. Создать локальную каноническую карточку

```bash
PYTHONPATH=src python scripts/ozon_prepare.py extract-wb \
  --input data/operations/migrations/wb-card.json \
  --nm-id WB_NM_ID \
  --output data/operations/migrations/canonical.json
```

## 3. Подобрать категорию Ozon

Сначала проверьте ключ Ozon без изменения данных:

```bash
PYTHONPATH=src python scripts/ozon_read.py auth-check
```

Получите дерево категорий и список кандидатов:

```bash
PYTHONPATH=src python scripts/ozon_read.py categories-fetch \
  --output data/operations/migrations/ozon-categories.json

PYTHONPATH=src python scripts/ozon_prepare.py categories-suggest \
  --canonical data/operations/migrations/canonical.json \
  --tree data/operations/migrations/ozon-categories.json \
  --nm-id WB_NM_ID \
  --limit 10 \
  --output data/operations/migrations/category-suggestions.json
```

Результат является подсказкой, а не разрешением на публикацию. Категорию и тип
Ozon необходимо подтвердить вручную: от них зависят обязательные
характеристики.

## 4. Получить характеристики и справочники

```bash
PYTHONPATH=src python scripts/ozon_read.py attributes-fetch \
  --category-id CATEGORY_ID \
  --type-id TYPE_ID \
  --output data/operations/migrations/attributes.json
```

Проверьте все обязательные характеристики, особенно бренд, страну
производства, маркировку и ТН ВЭД. Используйте только актуальные значения и ID
из справочника Ozon — не придумывайте их.

## 5. Подготовить изображения

```bash
PYTHONPATH=src python scripts/ozon_prepare.py media-prepare \
  --canonical data/operations/migrations/canonical.json \
  --nm-id WB_NM_ID \
  --public-base-url https://cdn.example.com/ozon-migration
```

Скрипт скачает изображения WB в WebP, проверит их и преобразует в JPEG.
Загрузите созданный каталог на свой сервер или в S3/CDN по пути из
`--public-base-url`. На сервере необходимо настроить HTTPS с действительным
сертификатом. Каждая итоговая ссылка должна быть доступна Ozon без cookies,
VPN и авторизации.

## 6. Добавить подтверждённые значения

Скопируйте `config/ozon/product-overrides.example.json` в каталог
`data/operations/migrations/` и заполните:

- цену и подтверждённый налоговый режим продавца;
- реальные размеры и вес товара в упаковке;
- бренд — `Нет бренда` только при фактическом отсутствии бренда;
- страну, указанную на товаре или упаковке;
- обязательные значения справочников Ozon, включая ТН ВЭД;
- результат проверки документов и ограничений.

Все файлы внутри `data/` исключены из Git.

## 7. Построить и проверить план

```bash
PYTHONPATH=src python scripts/ozon_prepare.py prepare \
  --canonical data/operations/migrations/canonical.json \
  --nm-id WB_NM_ID \
  --attributes data/operations/migrations/attributes.json \
  --category-id CATEGORY_ID \
  --type-id TYPE_ID \
  --type-name "ПОДТВЕРЖДЁННЫЙ ТИП" \
  --media-map data/operations/migrations/media/MEDIA_RUN/media-url-map.json \
  --overrides data/operations/migrations/overrides.json
```

Команда напечатает путь к плану, блокирующие ошибки, предупреждения и точную
строку подтверждения. План со статусом `blocked` или `manual_review`
подтвердить нельзя.

## 8. Подтвердить и загрузить одну карточку

```bash
PYTHONPATH=src python scripts/ozon_prepare.py approve \
  --plan data/operations/migrations/PLAN_ID/migration-plan.json \
  --confirm "APPROVE:PLAN_ID:HASH_PREFIX"
```

Переключитесь на отдельный Ozon Seller API-ключ с правом импорта товаров и
снова выполните `auth-check`. Только для одной утверждённой команды включите
защиту записи:

```bash
export OZON_ENABLE_WRITES=UPLOAD_APPROVED_PLANS

PYTHONPATH=src python scripts/ozon_upload.py upload \
  --plan data/operations/migrations/PLAN_ID/migration-plan.json \
  --approval data/operations/migrations/PLAN_ID/approval.json \
  --confirm "UPLOAD:PLAN_ID:HASH_PREFIX"

unset OZON_ENABLE_WRITES
```

Проверьте задачу импорта и результат модерации:

```bash
PYTHONPATH=src python scripts/ozon_read.py poll --task-id TASK_ID
PYTHONPATH=src python scripts/ozon_read.py product-check --offer-id OFFER_ID
```

## Проверка проекта

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Репозиторий опубликован открыто, но право копирования, изменения и
распространения кода не предоставляется, пока владелец не добавит отдельную
лицензию.
