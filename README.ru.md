<div align="center">

# Django XLSX Mailing Import

**Экономный по памяти импорт XLSX-рассылок с защитой от дублей и журналом доставки.**

[English](README.md) · **Русский**

[![CI](https://github.com/IgorNadein/django-xlsx-mail-import/actions/workflows/ci.yml/badge.svg)](https://github.com/IgorNadein/django-xlsx-mail-import/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/IgorNadein/django-xlsx-mail-import)](https://github.com/IgorNadein/django-xlsx-mail-import/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

Импорт XLSX в сохраняемые задания рассылки с возможностью продолжить отправку после
остановки. Транспорт записывает письмо в лог после случайной **задержки 5–20 секунд**,
как требует задание. Настоящие письма не отправляются.

## Краткий обзор

| Область | Реализация |
|---|---|
| Команды | `import_mailings` — импорт; `send_mailings` — продолжение отправки |
| Входные данные | openpyxl в режиме read-only, валидация и обработка пакетами |
| Дедупликация | Уникальный `external_id`, защита от конфликтов, неизменяемое содержимое |
| Отправка | Атомарное резервирование с токеном владельца; явное восстановление |
| Данные | PostgreSQL 17 в Docker; SQLite для локального запуска |
| Качество | 30 тестов на PostgreSQL, проверки SQLite, Ruff, строгий mypy, миграции |
| Измерения | [Воспроизводимый тест на 10 и 100 тысячах строк](docs/benchmark.ru.md) |

## Быстрый запуск

Требуются Python 3.10+ и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev --locked
uv run python manage.py migrate
uv run python manage.py seed_demo_users
uv run python manage.py generate_sample_xlsx samples/mailings.xlsx
uv run python manage.py import_mailings samples/mailings.xlsx
```

В примере два письма; перед **каждым** действительно выполняется ожидание 5–20 секунд.
Можно сначала быстро импортировать данные, затем отдельно запустить отправку:

```bash
uv run python manage.py import_mailings samples/mailings.xlsx --no-send --batch-size 500
# Подставьте UUID из строки "Import run":
uv run python manage.py send_mailings --run <UUID>
```

Повторный импорт исходного файла или запуск `send_mailings` отправляет оставшиеся
задания `pending`. Задания со статусом `sent` повторно не отправляются. Строка,
учтённая как `skipped`, может ссылаться на ещё не отправленное задание прошлого импорта.

### PostgreSQL и Docker

```bash
docker compose up --build --wait
docker compose exec web python manage.py seed_demo_users
docker compose exec web python manage.py generate_sample_xlsx /tmp/mailings.xlsx
docker compose exec web python manage.py import_mailings /tmp/mailings.xlsx
```

Web/health endpoint: `http://127.0.0.1:8016`. Каталог `samples/` подключён как `/data`
только для чтения. Зависимости образа устанавливаются из `uv.lock`.
При обновлении базы v0.1 сначала выполните `python manage.py migrate`, затем запускайте
воркеры. Миграция сохраняет задания, восстанавливает связи с импортами и переводит
старые задания `processing` без владельца в `uncertain`.

## Формат XLSX

| external_id | user_id | email | subject | message |
|---|---:|---|---|---|
| `welcome-001` | `1` | `alice@example.com` | `Welcome` | `Hello, Alice!` |
| `digest-002` | `2` | `bob@example.com` | `Weekly digest` | `Your report is ready.` |

- Первая строка содержит пять обязательных заголовков. Порядок и регистр не важны.
  Дополнительные колонки разрешены, одинаковые заголовки отклоняются. Читается активный лист.
- Все поля обязательны; email валидируется. `user_id` — существующий стандартный
  пользователь Django, целое число от 1 до 2 147 483 647.
- Полностью пустые строки игнорируются. Ошибка содержит номер строки и не отменяет
  обработку корректных данных. `processed = created + skipped + errors`.
- Содержимое уже сохранённого `external_id` не изменяется повторным импортом.
  Внутри пакета для вставки рассматривается первое синтаксически корректное вхождение.
- Размер пакета — от 1 до 1 000 строк, по умолчанию 500. Задания, принадлежность файлу
  и счётчики сохраняются в одной транзакции пакета. Сбой следующего пакета не стирает прогресс.

```text
Import run: 43ffde16-7f4f-4ac2-a250-44928c9e7f2f
Processed rows: 2
Created records: 2
Skipped records: 0
Erroneous rows: 0
Sent messages: 2
Delivery failures: 0
Current delivery states for this file: {'pending': 0, 'processing': 0, 'sent': 2, 'failed': 0, 'uncertain': 0}
```

## Архитектура

```mermaid
flowchart LR
    XLSX[Поток строк XLSX] --> BATCH[Валидация пакета]
    BATCH --> DB[(Задания, связи и прогресс)]
    DB --> CLAIM[Атомарное резервирование]
    CLAIM --> DELAY[Ожидание 5–20 секунд]
    DELAY --> LOG[Запись письма в лог]
    LOG --> STATE[Фиксация результата по токену]
```

```text
mailings/
├── models.py                 ImportRun, MailingRecord, ImportEntry и ограничения
├── services.py               Валидация XLSX и транзакции импорта
├── delivery.py               Резервирование, отправка и восстановление
├── management/commands/
│   ├── import_mailings.py    Импорт и отправка ожидающих заданий файла
│   └── send_mailings.py      Отправка сохранённых заданий без повторного чтения XLSX
├── migrations/               Схема и обновление данных v0.1
└── tests/                    Валидация, восстановление, миграции и конкуренция
scripts/benchmark_import.py   Изолированный синтетический замер
```

`ImportEntry` связывает каждый импорт со всеми заданиями его корректных строк,
включая уже существующие. Благодаря этому можно продолжить отправку после `--no-send`
или частично выполненного прошлого запуска. Счётчики импорта описывают входные строки;
итоги доставки вычисляются по текущим статусам связанных заданий и не устаревают
после восстановления. `imported` означает завершение чтения файла, а не всей отправки.

## Отправка и восстановление

| Статус | Значение | Следующий шаг |
|---|---|---|
| `pending` | Ещё не отправлено | Обычный запуск команды |
| `processing` | Задание занято воркером | Дождаться завершения |
| `sent` | Письмо записано в лог, успех сохранён | Не выбирается для повторной отправки |
| `failed` | Транспорт гарантирует отсутствие отправки | Явный `--retry-failed` |
| `uncertain` | Результат после сбоя неизвестен | Сначала проверить результат, затем `--retry-uncertain` |

```bash
uv run python manage.py send_mailings --run <UUID> --limit 100
uv run python manage.py send_mailings --run <UUID> --retry-failed
# Только после проверки результата и остановки старых воркеров:
uv run python manage.py send_mailings --run <UUID> --retry-uncertain
```

Ctrl+C во время имитации задержки возвращает задание в `pending`. Прерывание на границе
отправки переводит его в `uncertain`. Принудительное завершение процесса может оставить
`processing`; `--retry-uncertain` также подхватывает резервирования старше пяти минут.
Этот флаг может создать дубль, если письмо было отправлено до сбоя фиксации результата.
Гарантия exactly-once между базой и внешним транспортом не заявляется. Реальному
провайдеру нужен ключ идемпотентности либо API проверки результата.

Резервирование выполняется одним условным `UPDATE`. Во время ожидания и отправки нет
открытой транзакции или блокировки строки. Уникальный токен не позволяет старому воркеру
перезаписать результат новой попытки. На PostgreSQL можно одновременно запустить несколько
процессов `send_mailings --run <UUID>`: они резервируют разные задания. Каждый процесс
обрабатывает доступные задания и завершается; планировщика здесь нет. SQLite предназначена
для локальной работы с одним воркером.

## Большие файлы и ограничения

Строки читаются потоком, объекты пакета ограничены его размером. Используются групповые
запросы и массовая вставка; доставка читает ограниченные страницы идентификаторов.
Но openpyxl загружает таблицы общих строк и стилей, поэтому нельзя обещать память строго
O(размер пакета) для любого XLSX. [Методика и результаты замера](docs/benchmark.ru.md).

Отправка намеренно медленная: один воркер ждёт 5–20 секунд на письмо. Замер проверяет
**только импорт**, без задержек и отправки. Задания в базе отделяют разбор файла от
доставки без дополнительного Redis/Celery. Автоматические повторы с задержкой,
планировщик, реальный почтовый транспорт и ротация логов не входят в решение.
Лог имитации содержит адрес и текст письма; используйте синтетические данные.

## Проверки качества

```bash
make check
```

Ruff проверяет стиль и форматирование; далее запускаются строгий mypy, pytest,
системные проверки Django и проверка актуальности миграций. CI использует Python
3.10 и 3.12 с SQLite и отдельное задание с PostgreSQL 17. Проверяются конкурирующие
импорты, захват одного задания двумя воркерами, одновременная независимая отправка
и обновление старой схемы. На SQLite три теста конкуренции PostgreSQL явно пропускаются.

Полный набор тестов на отдельном PostgreSQL:

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=mailings \
POSTGRES_USER=mailings POSTGRES_PASSWORD=mailings uv run pytest
```

Роли базы нужно право создать временную тестовую БД. В тестах ожидание заменено,
но отдельно проверяется выбор и использование обязательной задержки 5–20 секунд.

## Лицензия

Проект распространяется по [лицензии MIT](LICENSE).
