<div align="center">

# 🦾 Третья рука

**Честная бухгалтерия портфеля** для ИИС и брокерского счёта Т-Инвестиций —
локально, без отправки данных третьим лицам.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-141%20passed-brightgreen?style=for-the-badge)](#тесты)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.5.0-blue?style=for-the-badge)](https://github.com/KosenkovPavel/third_hand)

</div>

## 💡 Что это

Приложение брокера показывает состояние (сколько денег, какой P&L за период), но не
отвечает на главный вопрос инвестора — **«что делать и почему»**. «Третья рука»
считает то, чего нет в приложении:

- **честный P&L** — берём у брокера (`expected_yield`), а не пересчитываем сами
  (наивный «стоимость − средняя» завышает прибыль облигаций на НКД);
- **годовую доходность (XIRR)** — с учётом дат и сумм взносов, а не простой процент;
- **разбивку дохода** — реализованный / нереализованный / дивиденды+купоны / комиссии+налоги;
- **налоги ИИС** — вычеты, прогресс до лимита, незаявленные вычеты, справка 3-НДФЛ;
- **план ребалансировки** — что докупить/продать под целевую структуру, по лотам;
- **прогнозы** — сложный процент, три сценария, траектория к цели.

## 🖥️ Три интерфейса — одно ядро

Все фасады строят один и тот же отчёт (`build_report()` → `ReportData`), а рендерят
его по-разному: rich-текст для терминала, JSON для агентов.

| Интерфейс | Как запустить | Для кого |
|-----------|---------------|----------|
| **CLI** | `python main.py --rebalance …` | автоматизация, cron |
| **TUI** | `python main.py` (без флагов) | человек без агента |
| **MCP** | `python -m src.frontends.mcp` | AI-агенты |

## 📦 Установка

Требуется Python 3.12+.

```bash
git clone https://github.com/KosenkovPavel/third_hand.git
cd third_hand
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env` впиши токен T-Invest:

```
TINKOFF_TOKEN=твой_токен
ACCOUNT_ID=id_счёта
```

Токен выдаётся в настройках Т-Инвестиций (раздел «Разработчикам»). Если `ACCOUNT_ID`
пуст — список счетов покажет `python main.py --account`.

## 🚀 Быстрый старт

```bash
python main.py                          # TUI — диалог-«окошко» (без флагов)
python main.py --rebalance              # ребалансировка под цель из профиля
python main.py --rebalance '{"Акции": 60, "Облигации": 40}' --invest 10000
python main.py --forecast --risk --market
python main.py --help                   # все флаги
```

## 🤖 MCP-сервер (для агентов)

```bash
python -m src.frontends.mcp
```

Сервер отдаёт агенту готовые вычисления (JSON), а не сырые данные — их даёт
официальный T-Invest MCP. Агент рассуждает, инструмент считает.

| Инструмент | Что отдаёт |
|-----------|-----------|
| `portfolio_report` | сводка + реализованный/нереализованный P&L + позиции |
| `rebalance_plan` | что докупить/продать под целевую структуру |
| `xirr` | годовая доходность (money-weighted) |
| `iis_deductions` | взносы, вычеты, прогресс, незаявленные вычеты |
| `forecast` | прогноз сценариев |
| `tax_report` | справка 3-НДФЛ за год |

```bash
claude mcp add third-hand -- .venv/bin/python -m src.frontends.mcp
```

## 🛡️ Честность цифр

Ключевой принцип: **агент и наивный код не должны считать деньги.** Поэтому:

- P&L берём у брокера (`expected_yield`), а не выводим из цены и средней;
- годовую доходность (XIRR) показываем только когда счёту не меньше 3 лет — на
  коротком горизонте она волатильна и обманчива;
- деньги — только `Decimal`, никогда `float`.

## 🧪 Тесты

```bash
pytest -q
```

Тесты идут без сети — все обращения к брокеру через заглушки.

## 📁 Структура

```
src/
├── frontends/    # cli, tui, mcp — фасады одного ядра
├── providers/    # tinkoff (адаптер) + протоколы AccountClient/MarketDataClient/AnalyticsClient
├── portfolio.py, accounting.py, xirr.py, …  # ядро: честные вычисления
└── report_builder.py / report_data.py        # build_report() → ReportData
main.py           # точка входа: диспетчер TUI/CLI
tests/            # юнит-тесты без сети
```

Подробнее об архитектуре и решениях — в вики репозитория.

## 📄 Лицензия

[MIT](LICENSE)
