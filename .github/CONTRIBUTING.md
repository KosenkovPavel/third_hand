# Вклад в проект «Третья рука»

Спасибо, что помогаешь проекту. Ниже — как быстро поднять окружение и внести изменение.

## Настройка окружения

```bash
git clone <repo-url>
cd third_hand
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # впиши свой токен T-Invest
```

## Запуск

```bash
python main.py                # TUI (без флагов — диалог)
python main.py --rebalance    # CLI (флаги)
python -m src.frontends.mcp   # MCP-сервер
```

## Тесты

Тесты идут **без сети** — все обращения к брокеру через заглушки. Токен не нужен.

```bash
pytest -q
```

Перед PR убедись, что `pytest -q` зелёный.

## Структура

```
src/
├── frontends/    # cli, tui, mcp — фасады одного ядра
├── providers/    # tinkoff (адаптер) + протоколы AccountClient/MarketDataClient/AnalyticsClient
├── portfolio.py, accounting.py, xirr.py, …  # ядро: честные вычисления
└── report_builder.py / report_data.py        # build_report() → ReportData
```

Принципы: ядро считает и возвращает `ReportData`; фасады рендерят (rich — CLI/TUI,
JSON — MCP); домен не знает SDK брокера (только протоколы).

## Стиль

- Деньги — только `Decimal`, никакого `float`.
- Сообщения и docstring'и — на русском.
- Ошибки сети не роняют отчёт — попадают в `data_warnings`.
