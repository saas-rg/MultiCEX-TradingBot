# Gate.io Trading Bot

## Версии

- **v0.8.0 (2025-09-27)**
  - Multi-exchange registry (gate, htx) + lazy imports.
  - Pairs config with exchange binding; web admin supports exchange select.
  - Engine routes ops through exchange proxy; per-exchange cancel_all at start/pause/new BUY.
  - Reporting: exchange column in CSV; NET per (exchange+pair); JSON summary endpoint.
  - HTX Spot adapter (MVP): prices/limits/orders/balances/trades.
  - Verified concurrent run: Gate testnet + HTX spot.

- **v0.7.3 (2025-09-26)**
  - Подготовка к мультибиржевости;
  - DB: колонка bot_pairs.exchange (+ автомиграция);
  - params: list_pairs/upsert_pairs с exchange (back-compat);
  - exchange_proxy + GateV4Adapter: fetch_trades;
  - exchanges/gate: fetch_trades;
  - reporting через прокси;
  - webapp показывает exchange, UI пока не редактирует;
  - Поведение торговли не изменилось (только Gate).

- **v0.7.2 (2025-09-25)**
  - Рефакторинг конфигурации: пары в формате EXCHANGE=...,PAIR=...;
  - CSV и телеметрия: добавлена колонка exchange (по умолчанию = gate);
  - Веб-админка: выведена биржа для пары (только gate).
  
- **v0.7.1 (2025-09-25)**
  - Ввод ExchangeAdapter$
  - Обёртка gate_v4.py;
  - Движок работает только с этим интерфейсом;
  - Дополнительно: логирование и телеметрия событий автокоррекции объёма покупки при нехватке средств на балансе.

- **v0.7.0 (2025-09-19)**
  - CSV: отрицательный BUY quote_value, итоговая строка NET;
  - NET в тексте отчёта;
  - heartbeat при старте.

- **v0.6.0 (2025-09-19)**
  - Мультипарность: до 5 пар одновременно с независимыми настройками (PAIR, QUOTE, BASE, DEV %, GAP MODE).
  - Веб-интерфейс: админка для управления парами и глобальными параметрами.
  - Телеметрия в Telegram:
    - события жизненного цикла (start/stop, pause/resume);
    - изменения параметров и торговых пар;
    - периодические отчёты (CSV в группу) с окнами BUY/SELL;
    - heartbeat раз в 60 минут и алерт при тишине >90 минут.
  - Отчётность: формирование CSV по чётким интервалам (1m/5m/10m/15m/30m/60m).
  - Параллельное выставление лимитников и рыночный слив → уменьшена задержка.
  - Неблокирующие фоновые отчёты → торговля не тормозит при отправке CSV.
  - Доработан дренаж «пыли».
  - Оптимизирована работа с API (http + подпись запросов).

- **v0.5.0-baseline (2025-09-08)**
  - Простая стратегия (лимитный BUY, рыночный SELL).
  - Логирование.
  - Минимальный web-UI.
  - Не работает адекватная оценка «слива» малых остатков.

Baseline:
Ветка: baseline-2025-09-08-12-17

Тег/релиз: v0.5.0-baseline

Коммит: chore: initial import v0.5.0 (modularized)

Что работает:
- выставление лимитников
- слив по рынку
- простое логирование
- простой веб-UI
- НЕ РАБОТАЕТ оценка слива (слишком малый остаток)

Как откатиться локально:

git fetch --all --tags
git checkout baseline-2025-09-08


Как откатить Heroku: выбрать ветку baseline-2025-09-08-12-17 в Manual deploy и нажать Deploy.
