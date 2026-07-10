# Шпаргалка по логике SMC Sniper Bot

Эта шпаргалка объясняет простыми словами, что бот ищет на графике, как читает структуру и почему начисляет или не начисляет баллы.

## 1. Как читать брифинг

Пример строки:

```text
💎 BTC | Сетап: LONG 🟢 | Score: 40/100 | Ignore
📊 Тренд (4H): ВВЕРХ ↗️ | +10 (...)
⚙️ Структура: 0 (...)
💧 Ликвидность: +20 (...)
🎯 FVG: 0 (...)
📈 Объем: +10 (...)
🌍 Макро: 0 (...)
```

Бот складывает несколько блоков:

| Блок | Что означает |
| --- | --- |
| `Тренд` | Направление старшего 4H контекста |
| `Структура` | BOS или CHoCH на 1H/15m |
| `Ликвидность` | SFP, то есть снятие ликвидности |
| `FVG` | Зона imbalance / fair value gap |
| `Объем` | Подтверждение RVOL |
| `Макро` | DXY/SPX/BTC.D контекст |

Главная идея:

```text
Хороший сетап = направление + структура + ликвидность/POI + качество + объем + макро.
```

Если есть только один сильный фактор, бот обычно оставляет `Ignore`.

### Новый компактный Telegram-отчет

Теперь отчет разделяет две вещи:

```text
Score = сколько баллов получил сетап.
Diagnostics = что именно рынок уже показал и чего еще не хватает.
```

Пример:

```text
💎 ADA | NO TRADE — Context only | 40/100 | Ignore
📊 4H: ВВЕРХ по EMA99 | +10 (...) | ADX 20.4
⚙️ Structure: +10 (1H BOS Q95 DR1.61 BR0.88 only)
💧 Sweep/SFP: 0
📈 Volume: +5 (1H BOS volume: RVOL 1.72, Q95)
🎯 FVG: 0 (FVG close invalidated после retest)
⚖️ P/D: OK (4H discount normal...)
🧭 Scenario: wait_sweep C35 (next: liquidity_sweep)
🚧 Gates: KZ PASS | P/D PASS | Sweep FAIL | Trigger FAIL | FVG FAIL | SM WAIT | Macro MIXED
```

Как читать:

| Строка | Что говорит |
| --- | --- |
| `NO TRADE — ...` | главная причина, почему нет сделки |
| `Sweep/SFP` | был ли свежий захват ликвидности |
| `Liq` | карта ближайшей buy-side / sell-side liquidity, если включен audit-режим |
| `Scenario` | этап State Machine: чего ждет сценарий дальше |
| `Gates` | обязательные фильтры, которые должны пройти до A+ |

Важно:

```text
Не каждый найденный элемент должен давать баллы.
Например, ближайшая ликвидность полезна для понимания карты,
но сама по себе не является входом.
```

### Категории NO TRADE

| Категория | Простыми словами |
| --- | --- |
| `Neutral HTF` | старший рынок неоднозначный |
| `P/D block` | BUY в premium или SELL в discount / equilibrium |
| `Countertrend` | сетап против 4H EMA99 |
| `FVG invalid` | FVG пробит закрытием свечи |
| `Missing structure` | нет BOS/CHoCH |
| `Context only` | есть только 1H контекст, нет 15m триггера |
| `Waiting confirmation` | есть триггер, но нет POI/SFP подтверждения |
| `Missing sweep/POI` | нет свежего sweep или FVG/POI контекста |
| `Scenario gate` | баллы есть, но State Machine последовательность не завершена |

### FVG в отчете

Теперь отчет различает:

```text
wick violation only / Q penalty
```

и:

```text
FVG close invalidated после retest
```

Разница важная:

```text
wick violation = цена проколола зону тенью, качество снижено;
close invalidated = свеча закрылась за зоной, FVG больше не считается рабочим.
```

## 2. Главные сокращения

| Сокращение | Расшифровка | Простыми словами |
| --- | --- | --- |
| `HH` | Higher High | Новый максимум выше прошлого |
| `HL` | Higher Low | Новый минимум выше прошлого |
| `LH` | Lower High | Новый максимум ниже прошлого |
| `LL` | Lower Low | Новый минимум ниже прошлого |
| `BOS` | Break of Structure | Продолжение структуры через пробой |
| `CHoCH` | Change of Character | Подтвержденная смена характера рынка |
| `SFP` | Swing Failure Pattern | Цена сняла ликвидность за swing и вернулась обратно |
| `FVG` | Fair Value Gap | Имбаланс / незаполненная зона между свечами |
| `POI` | Point of Interest | Зона интереса для реакции цены |
| `ATR` | Average True Range | Средняя волатильность |
| `RVOL` | Relative Volume | Объем относительно среднего |
| `ADX` | Average Directional Index | Сила тренда |
| `EMA99` | Exponential Moving Average 99 | Базовая 4H трендовая линия |
| `Displacement` | Импульс | Насколько свеча реально толкнула цену |
| `Confidence` | Уверенность | Насколько алгоритм уверен, что событие определено корректно |
| `EQH` | Equal Highs | Равные максимумы, над которыми лежит buy-side liquidity |
| `EQL` | Equal Lows | Равные минимумы, под которыми лежит sell-side liquidity |
| `P/D` | Premium / Discount | Где цена находится внутри swing range |

## 3. Метрики в отчете

### Структура

Пример:

```text
15m BOS Q100 DR3.52 BR0.98
```

| Поле | Значение |
| --- | --- |
| `Q100` | quality_score от 0 до 100 |
| `DR3.52` | displacement ratio: тело свечи / ATR |
| `BR0.98` | body ratio: тело свечи / весь диапазон свечи |
| `C94` | confidence для CHoCH, если это CHoCH |

### SFP

Пример:

```text
SFP Q84 D0.46 R99
```

| Поле | Значение |
| --- | --- |
| `Q84` | качество SFP |
| `D0.46` | liquidity depth: насколько глубоко цена вышла за swing в ATR |
| `R99` | rejection strength: сила возврата обратно |

### FVG

Пример:

```text
FVG Q80
```

| Поле | Значение |
| --- | --- |
| `Q80` | качество зоны FVG |
| `tested=True` | зона уже тестировалась |
| `invalidated=True` | зона полностью пробита / заполнена |
| `overlap_percent` | насколько зона уже перекрыта ценой |
| `age_bars` | возраст зоны в свечах |

## 4. Displacement Engine

`Displacement` это общий двигатель оценки импульса.

Простыми словами:

```text
Бот смотрит не просто "свеча зеленая или красная",
а насколько свеча сильная, чистая и где она закрылась.
```

Теперь один и тот же расчет используется в:

```text
BOS
CHoCH
SFP
FVG
финальном подтверждении качества движения
```

### Что считает Displacement

| Поле | Формула | Простыми словами |
| --- | --- | --- |
| `body` | `abs(close - open)` | размер тела свечи |
| `candle_range` | `high - low` | весь диапазон свечи |
| `body_ratio` | `body / candle_range` | насколько свеча телом, а не тенями |
| `atr_ratio` | `body / ATR` | насколько тело большое относительно волатильности |
| `close_position` | зависит от направления | насколько хорошо свеча закрылась у края |
| `volume_ratio` | `RVOL` или `None` | был ли повышенный объем |
| `score` | 0-100 | итоговая сила импульса |
| `valid` | `score >= 70` | импульс достаточно сильный или нет |

### Close Position

Для bullish свечи:

```text
close_position = (close - low) / candle_range
```

Хорошо, когда close ближе к high.

Для bearish свечи:

```text
close_position = (high - close) / candle_range
```

Хорошо, когда close ближе к low.

### Как считается score

```text
body_ratio дает до 35 баллов
atr_ratio дает до 25 баллов
close_position дает до 25 баллов
RVOL дает до 15 баллов
```

Итог:

```text
score >= 70 -> valid displacement
score < 70 -> слабый импульс
```

### Хороший displacement

```text
Большое тело.
Маленькие лишние тени.
Закрытие около high для bullish или около low для bearish.
Тело заметно больше обычного ATR.
RVOL повышенный.
```

### Плохой displacement

```text
Doji.
Маленькое тело.
Большие тени.
Закрытие в середине свечи.
ATR ratio маленький.
Объема нет.
```

Если `ATR = 0` или `candle_range = 0`, бот не падает.
Он возвращает безопасные нули и такой импульс обычно считается invalid.

## 5. Confidence Model

`Confidence Model` это общий формат результата для сигналов.

Простыми словами:

```text
Бот должен понимать не только "событие есть",
но и насколько оно качественное и насколько ему можно доверять.
```

### Два разных понятия

| Поле | Что означает |
| --- | --- |
| `quality_score` | качество самого события |
| `confidence` | уверенность алгоритма, что событие распознано правильно |

Пример:

```text
BOS может иметь quality_score 90,
потому что свеча сильная.
```

Но confidence может быть ниже:

```text
если swing-данных мало
или рядом есть конфликтующая структура.
```

### Базовые result-объекты

| Объект | Для чего |
| --- | --- |
| `BaseSignalResult` | общий результат сигнала |
| `StructureResult` | структура, BOS/CHoCH/Neutral |
| `FVGResult` | зона FVG |
| `SFPResult` | swing failure pattern |
| `SetupContext` | общий контекст сетапа |

Все `quality_score` и `confidence` ограничены:

```text
0..100
```

Старый код может читать поля привычно:

```text
result.detected
result.get("quality_score")
result["confidence"]
```

Главная идея:

```text
сильное событие и надежно распознанное событие - не одно и то же.
```

## 6. Market State: Bullish, Bearish, Neutral

Раньше бот почти всегда выбирал направление. Теперь есть 3 состояния:

```text
Bullish
Bearish
Neutral
```

В брифинге HTF-контекст разделен на 3 строки:

```text
4H Bias = направление цены относительно EMA99
4H Structure = HH/HL, LH/LL или Neutral по swing-структуре
ADX = сила тренда и +DI/-DI
```

Рабочие 1H/15m swing-и используются ниже для контекста и триггеров, но не должны сами превращать 4H structure в Bullish/Bearish.

### Bullish

Рынок считается bullish, если структура подтверждает рост:

```text
HH + HL
```

То есть:

```text
максимумы растут
минимумы растут
```

### Bearish

Рынок считается bearish, если структура подтверждает падение:

```text
LH + LL
```

То есть:

```text
максимумы падают
минимумы падают
```

### Neutral

Neutral значит:

```text
Рынок непонятный. Сделку не ищем.
```

Бот возвращает Neutral, если:

| Причина | Что это значит |
| --- | --- |
| `Conflicting swing structure` | Последние swing-переходы дают одновременно `HH` и `LL` |
| `No confirmed swing structure` | Мало подтвержденных swing-ов |
| `ADX below neutral threshold` | ADX ниже 18, тренд слабый |
| `Range too narrow` | Диапазон слишком узкий относительно ATR |
| `Compressed swing structure` | Есть `LH` и `HL`, цена сжимается |
| `Conflicting recent BOS` | Последние BOS противоречат друг другу |

Если Neutral:

```text
Score = 0
direction = NEUTRAL
SFP/BOS/FVG могут быть показаны только как диагностика, но не используются для сделки
A+ уведомление не отправляется
```

Исключение:

```text
Если ADX ниже 18, но уже есть сильная связка
SFP + CHoCH + BOS + displacement,
бот может продолжить анализ как Watchlist only.
A+ в таком режиме заблокирован.
```

## 7. Swing-структура

Свингами бот считает подтвержденные локальные максимумы и минимумы.

### Восходящая структура

```text
HH
HL
HH
HL
```

Перевод:

```text
Цена делает максимумы выше и минимумы выше.
Покупатель контролирует рынок.
```

### Нисходящая структура

```text
LH
LL
LH
LL
```

Перевод:

```text
Цена делает максимумы ниже и минимумы ниже.
Продавец контролирует рынок.
```

### Конфликтная структура

```text
HH + LL
```

Перевод:

```text
Последние swing-и одновременно показывают силу вверх и вниз.
Бот не угадывает направление и ставит Neutral.
```

## 8. BOS

`BOS` это Break of Structure.

Простыми словами:

```text
Цена пробила важный swing по направлению структуры.
```

### Bullish BOS

```text
Была восходящая структура.
Цена импульсно закрылась выше swing high.
```

### Bearish BOS

```text
Была нисходящая структура.
Цена импульсно закрылась ниже swing low.
```

### Что бот проверяет для BOS

Бот не считает BOS просто по факту прокола уровня. Нужны:

| Критерий | Порог |
| --- | --- |
| `quality_score` | минимум 70 |
| `body_ratio` | минимум 0.55 |
| `displacement_ratio` | минимум 0.8 ATR |
| `close_buffer` | закрытие дальше уровня минимум на 0.1 ATR |
| `close_position` | закрытие в правильной части свечи, минимум 0.65 |
| `RVOL` | сильный объем от 1.5 |
| `opposite_wick_ratio` | не больше 0.35 |
| `hold_confirmed` | если включено, цена не должна сразу вернуться |

Важно:

```text
body_ratio, displacement_ratio и volume_confirmed теперь приходят из Displacement Engine.
То есть BOS, CHoCH, SFP и FVG говорят на одном языке импульса.
```

Важно:

```text
Если структура перед пробоем неоднозначная,
бот не угадывает направление по последнему swing high/low.
Ambiguous structure = BOS не засчитывается.
```

### Хороший BOS выглядит так

```text
Большое тело свечи.
Свеча закрылась далеко за swing.
Закрытие находится в правильной части свечи.
Маленькая противоположная тень.
Объем выше среднего.
Цена не вернулась сразу обратно.
```

### Плохой BOS

```text
Свеча чуть-чуть закрылась за swing.
Тело маленькое.
Тень большая.
Нет объема.
Цена сразу вернулась обратно.
```

Такой BOS бот не засчитает.

В отчете:

```text
BOS Q95 DR1.61 BR0.88 CP0.93
```

Где:

```text
CP = close position.
Чем ближе к 1, тем лучше закрытие свечи.
```

## 9. CHoCH

`CHoCH` это Change of Character.

Простыми словами:

```text
Рынок не просто пробил уровень, а реально сменил поведение.
```

Один пробой теперь не считается CHoCH.

### Bearish CHoCH

Сначала рынок растет:

```text
HH
HL
HH
HL
```

Потом происходит:

```text
пробой HL
формирование LH
пробой нового LL
```

Только после этого бот считает:

```text
bearish_choch
```

### Bullish CHoCH

Сначала рынок падает:

```text
LH
LL
LH
LL
```

Потом происходит:

```text
пробой LH
формирование HL
пробой нового HH
```

Только после этого бот считает:

```text
bullish_choch
```

### Метрики CHoCH

| Поле | Значение |
| --- | --- |
| `quality_score` | качество импульса пробоя |
| `confidence` | уверенность смены структуры |
| `swing_sequence_valid` | правильная ли sequence swing-ов в строгом порядке |

Порог:

```text
quality_score >= 70
confidence >= 75
```

Импульс пробоя в CHoCH оценивается тем же Displacement Engine, что и BOS.
Один слабый пробой не считается сменой характера.

Важно:

```text
CHoCH проверяет не просто наличие HH/HL/LL/LH где-то в истории,
а порядок этих swing-меток.

Bearish path: HH -> HL -> LL -> LH
Bullish path: LL -> LH -> HH -> HL
```

## 10. Liquidity Map

`Liquidity Map` это карта уровней, где может лежать ликвидность.

Простыми словами:

```text
Бот смотрит не только на ближайший swing,
а на всю карту мест, где стоят стопы и отложенные ордера.
```

В отчете это отдельная диагностическая строка:

```text
Liquidity Map: Buy: buy_side 0.1750 S25 D1.20ATR T1 fresh | Sell: equal_lows 0.1650 S40 D0.80ATR T3 fresh
```

Важно:

```text
Строка "Ликвидность" в score = подтвержденный SFP/Sweep, который может дать баллы.
Строка "Liquidity Map" = карта ближайших и сильных пулов.
Карта сама по себе не разгоняет score, но SFP теперь может проверяться против ее свежих уровней.
```

### Какие уровни ищет бот

| Тип | Что значит |
| --- | --- |
| `equal_highs` | два или больше swing high почти на одном уровне |
| `equal_lows` | два или больше swing low почти на одном уровне |
| `buy_side` | ликвидность выше swing high |
| `sell_side` | ликвидность ниже swing low |
| `internal` | ликвидность внутри текущего диапазона |
| `external` | ликвидность за границами текущего диапазона |
| `old_high` | старый значимый максимум |
| `old_low` | старый значимый минимум |

### Как читать строку Liquidity Map

```text
S40 = strength уровня 40/100
D0.80ATR = расстояние до уровня 0.80 ATR
T3 = 3 касания
fresh = уровень еще не снят
swept = уровень уже снят
```

### Equal Highs

```text
Если 2+ swing high находятся в пределах 0.15 ATR,
бот считает это equal highs.
```

Это обычно buy-side liquidity:

```text
над равными максимумами часто стоят стопы шортистов.
```

### Equal Lows

```text
Если 2+ swing low находятся в пределах 0.15 ATR,
бот считает это equal lows.
```

Это обычно sell-side liquidity:

```text
под равными минимумами часто стоят стопы лонгистов.
```

### Internal vs External

Internal liquidity:

```text
уровни внутри текущего range.
Они важны, но слабее.
```

External liquidity:

```text
уровни за пределами range.
Они важнее, потому что там крупнее пул стопов.
```

### Swept

`swept=True` значит:

```text
уровень уже сняли.
Он остается на карте,
но больше не считается свежим источником ликвидности.
```

Такие уровни получают штраф к `strength` и не должны использоваться как свежая причина для входа.

### Strength

Каждый liquidity level получает силу от 0 до 100.

На силу влияет:

```text
сколько раз уровень тестировали
external это или internal
насколько уровень свежий
как далеко он от текущей цены
снят он уже или нет
```

Пример:

```text
Equal Highs, 3 касания, рядом с ценой, не swept = сильный buy-side pool.
Old High, далеко и уже swept = слабый уровень.
```

## 11. Premium / Discount

`Premium / Discount` это фильтр положения цены внутри диапазона.

Простыми словами:

```text
BUY лучше искать ниже середины диапазона.
SELL лучше искать выше середины диапазона.
```

### Как строится диапазон

Бот берет последний значимый swing range:

```text
range_high = последний значимый Swing High
range_low = последний значимый Swing Low
equilibrium = середина диапазона
```

Приоритет таймфреймов:

```text
1. 4H range
2. 1H range
3. 15m range
```

То есть бот сначала пытается понять положение цены в старшем контексте.

### Зоны

| Зона | Условие | Что значит |
| --- | --- | --- |
| `discount` | цена ниже equilibrium | зона лучше для BUY |
| `premium` | цена выше equilibrium | зона лучше для SELL |
| `equilibrium` | цена рядом с серединой | слабая зона для входа |

Tolerance для equilibrium:

```text
2% от ширины swing range вокруг equilibrium
```

Но одного слова `discount` или `premium` мало.
Бот также показывает глубину зоны:

| Depth | Расстояние от EQ по ширине range | Что значит |
| --- | --- | --- |
| `equilibrium` | до 2% | нет edge, сделки блокируются |
| `shallow` | 2-20% | правильная половина range, но слабая зона |
| `normal` | 20-35% | нормальная discount/premium зона |
| `deep` | 35%+ | глубокая discount/premium зона |

Shallow-зона не считается A+ контекстом:

```text
shallow discount для BUY = направление разрешено,
но A+ блокируется и сетап максимум Watchlist.
```

Зачем:

```text
LINK discount 5% range и ADA discount 28% range
не должны выглядеть одинаково сильными.
```

Если цена близко к середине диапазона, бот возвращает:

```text
zone = equilibrium
valid_for_buy = False
valid_for_sell = False
```

### Фильтр сделок

| Сделка | Хорошая зона | Плохая зона |
| --- | --- | --- |
| BUY | `discount` | `premium` или `equilibrium` |
| SELL | `premium` | `discount` или `equilibrium` |

Если зона плохая:

```text
Score = 0
Decision = Ignore
```

Пример:

```text
P/D: BLOCK (4H premium normal +10.00% от EQ, 25.00% range, S75)
```

Это значит:

```text
BUY заблокирован, потому что цена слишком высоко в диапазоне.
```

Пример:

```text
P/D: OK (4H discount normal -8.00% от EQ, 20.00% range, S75, range 0.1381-0.1999)
```

Это значит:

```text
BUY разрешен по зоне диапазона.
```

Пример shallow:

```text
P/D: WATCHLIST (4H discount shallow -2.50% от EQ, 10.00% range, S35, shallow zone caps A+ 86->69)
```

Это значит:

```text
BUY не заблокирован полностью,
но зона слишком близко к equilibrium для A+.
```

## 12. SFP

`SFP` это Swing Failure Pattern.

Простыми словами:

```text
Цена вышла за swing, собрала стопы/ликвидность и вернулась обратно.
```

### Bearish SFP

```text
Цена пробила swing high вверх.
Потом закрылась обратно ниже swing high.
```

Это намекает:

```text
Покупателей заманили, ликвидность сняли, цена может пойти вниз.
```

### Bullish SFP

```text
Цена пробила swing low вниз.
Потом закрылась обратно выше swing low.
```

Это намекает:

```text
Продавцов заманили, ликвидность сняли, цена может пойти вверх.
```

### Что бот проверяет для SFP

| Критерий | Порог |
| --- | --- |
| `quality_score` | минимум 65 |
| `liquidity_depth` | минимум 0.08 ATR |
| `rejection` | минимум 0.15 ATR |
| `displacement` | минимум 0.2 ATR |
| `RVOL` | сильный объем от 1.5 |
| `opposite_wick_ratio` | не больше 0.35 |
| `level_strength` | минимум 35, если SFP найден через Liquidity Map |

Для SFP Displacement Engine оценивает свечу возврата внутрь диапазона:

```text
важно не только проколоть swing,
важно закрыться обратно сильно и убедительно.
```

### SFP через Liquidity Map

Раньше SFP проверялся только против последнего 1H swing high/low.
Теперь бот сначала проверяет свежие уровни из Liquidity Map:

```text
equal_highs / equal_lows
buy_side / sell_side
old_high / old_low
internal / external
```

Для long важен sweep sell-side liquidity:

```text
equal_lows, sell_side, old_low, external ниже цены
```

Для short важен sweep buy-side liquidity:

```text
equal_highs, buy_side, old_high, external выше цены
```

Важно:

```text
если уровень уже swept, бот не использует его как свежий источник ликвидности.
```

Итоговый SFP quality теперь учитывает не только свечу, но и качество снятого уровня:

```text
65% качество SFP-свечи
35% strength уровня ликвидности
```

Если Liquidity Map не дала валидного уровня, бот оставляет старый fallback:

```text
проверить SFP против последнего подтвержденного 1H swing.
```

### Градация SFP в скоринге

| Тип SFP | Условие | Баллы за ликвидность |
| --- | --- | --- |
| Strong | `Q >= 80`, `R >= 75`, `D >= 0.15` | `+20` |
| Medium | `Q >= 70`, но не strong | `+10` |
| Weak | `D < 0.15` или `R < 60` | `+5` |

Объем по SFP:

```text
+10 за объем дается только strong SFP.
```

Пример:

```text
SFP Q84 D0.46 R99
```

Это strong SFP:

```text
глубина нормальная
rejection сильный
можно дать +20 за ликвидность и +10 за объем
```

Пример SFP через карту ликвидности:

```text
SFP Q82 D0.32 R88 equal_lows S85
```

Это значит:

```text
сняли equal lows
strength уровня 85/100
SFP-свеча подтвердила возврат внутрь
```

Пример:

```text
SFP Q76 D0.09 R93
```

Это слабый SFP:

```text
rejection сильный, но прокол очень мелкий
только +5
объем не засчитывается
```

Пример:

```text
SFP Q74 D0.87 R40
```

Это слабый SFP:

```text
глубина хорошая, но rejection слабый
только +5
```

## 13. FVG

`FVG` это Fair Value Gap.

Простыми словами:

```text
Цена прошла участок слишком быстро и оставила imbalance.
Потом цена может вернуться туда для ретеста.
```

### Bullish FVG

```text
Low текущей свечи выше High свечи две свечи назад.
```

Это зона поддержки.

### Bearish FVG

```text
High текущей свечи ниже Low свечи две свечи назад.
```

Это зона сопротивления.

### Что влияет на качество FVG

| Критерий | Что значит |
| --- | --- |
| размер относительно ATR | чем больше imbalance, тем сильнее зона |
| displacement свечи | была ли импульсная свеча |
| RVOL | был ли повышенный объем |
| возраст зоны | слишком старая зона слабее |
| overlap_percent | насколько зона уже заполнена |
| invalidated | полностью ли зона пробита |
| wick_violated | зона пробита тенью, но не телом |
| close_invalidated | зона пробита закрытием свечи |
| retest_depth | насколько глубоко был ретест |
| retest_count | сколько раз зону тестировали |

Displacement для FVG считается по импульсной свече, которая создала gap.
Если gap появился без нормального импульса, качество зоны ниже.

### Главное правило FVG

```text
Если FVG пробит закрытием свечи, он invalidated.
quality_score = 0.
Бот не использует его как POI.

Если зона проколота только тенью,
она получает сильный штраф качества,
но не считается полностью invalidated.
```

### Пример

```text
FVG Q80
```

Это значит:

```text
Зона еще валидна.
Качество 80 из 100.
Ее можно учитывать в сетапе.
```

## 14. POI

`POI` это Point of Interest.

Простыми словами:

```text
Место, где мы ждем реакцию цены.
```

У нас POI чаще всего:

```text
FVG
SFP
важный swing
fresh liquidity level
```

Если структура происходит `in POI`, это сильнее, чем структура сама по себе.

## 15. Объем

Объем оценивается через `RVOL`.

```text
RVOL >= 1.5 = объем выше среднего
```

Но теперь объем не начисляется вслепую.

Важно:

```text
Объем сам по себе не является сигналом.
Он только подтверждает конкретное событие:
SFP, BOS, CHoCH или FVG displacement.
```

1H RVOL считается отдельно на настоящих 1H свечах:

```text
1H volume / средний 1H volume за 20 свечей.
```

Он не считается как среднее из 15m RVOL.

### Absorption / Exhaustion warning

Высокий объем не всегда хороший.

Плохой высокий объем:

```text
RVOL >= 2.0
body_ratio < 0.35
close_position < 0.55
```

Это значит:

```text
объем большой,
но свеча не смогла уверенно закрыться в сторону движения.
Возможны absorption / exhaustion.
```

В таком случае бот ставит:

```text
absorption_warning = True
```

И не дает volume bonus за это событие.

### Объем по SFP

```text
+10 только если SFP strong-tier.
```

Пример:

```text
Объем: +10 (Сильный SFP volume confirmation: RVOL 2.00, Q84)
```

Если объем есть, но SFP не strong:

```text
Объем: 0 (RVOL 2.00 есть, но SFP не strong-tier: Q70)
```

Если объем высокий, но свеча плохая:

```text
Объем: 0 (RVOL 2.50 высокий, но слабое закрытие / absorption warning)
```

### Объем по BOS/CHoCH

| Ситуация | Баллы |
| --- | --- |
| BOS/CHoCH с POI/SFP confirmation | `+10` |
| BOS без POI/SFP, но Q >= 90 | `+5` |
| BOS без POI/SFP и Q < 90 | `0` |

Примеры:

```text
Объем: +5 (1H BOS volume: RVOL 1.72, Q95)
Объем: 0 (RVOL 1.64 есть, но 1H структура Q82 < Q90)
```

Почему так:

```text
Даже сильный BOS без места входа не должен слишком сильно разгонять score.
```

### Объем по FVG

FVG quality учитывает RVOL импульсной свечи.

Но если на импульсной свече есть absorption warning:

```text
volume_confirmed = False
quality_score не получает volume bonus
```

## 16. State Machine

`State Machine` это конечный автомат сценария.

Простыми словами:

```text
Бот проверяет не просто набор признаков,
а правильный порядок торговой истории.
```

State Machine подключен как сценарный gate для A+:

```text
State: waiting_for_choch C25 (2/8, next: choch_confirmed)
```

Это значит:

```text
бот показывает, на каком шаге сценария находится рынок,
и запрещает A+, если сценарий не дошел до signal_ready.
```

Важно:

```text
State Machine не создает сигнал.
Он отвечает на вопрос: "это правильная Sniper-последовательность?"
Score отвечает на вопрос: "насколько качественная эта последовательность?"
```

События проверяются по хронологии:

```text
sweep < CHoCH < BOS < FVG created < FVG retest < displacement
```

Если события есть, но порядок неправильный:

```text
State = invalidated
A+ запрещен
```

Финальный displacement не подменяется BOS-свечой:

```text
BOS подтверждает структуру.
Displacement после FVG retest подтверждает вход.
```

### BUY-сценарий

Правильный порядок:

```text
1. HTF context bullish или HTF discount
2. Цена вошла в HTF POI
3. Sell-side liquidity sweep
4. Bullish CHoCH
5. Bullish BOS
6. Bullish FVG created
7. Retest FVG
8. Bullish displacement confirmation
9. Signal ready
```

### SELL-сценарий

Правильный порядок:

```text
1. HTF context bearish или HTF premium
2. Цена вошла в HTF POI
3. Buy-side liquidity sweep
4. Bearish CHoCH
5. Bearish BOS
6. Bearish FVG created
7. Retest FVG
8. Bearish displacement confirmation
9. Signal ready
```

### Почему это важно

Плохой порядок инвалидирует сценарий:

| Нарушение | Что делает бот |
| --- | --- |
| FVG появился до CHoCH | `INVALIDATED` |
| BOS появился до liquidity sweep | `INVALIDATED` |
| Retest был до FVG creation | `INVALIDATED` |
| HTF context Neutral | `INVALIDATED` |
| BUY в premium | `INVALIDATED` |
| SELL в discount | `INVALIDATED` |

### Таймауты

Каждый этап имеет окно ожидания.

Пример:

```text
после liquidity sweep CHoCH должен появиться быстро.
если CHoCH не появился в срок, сценарий устарел.
```

Таймауты нужны, чтобы бот не склеивал старые события в новый сетап.

### Главное правило State Machine

```text
Сильные признаки в неправильном порядке - это не сетап.
```

## 17. Скоринг

### Trend

| Условие | Баллы |
| --- | --- |
| сильный тренд совпадает со сделкой | `+25` |
| цена по тренду, но импульс слабый/откат | `+10` |
| контртренд против 4H EMA99 | `0` |

### Structure

| Условие | Баллы |
| --- | --- |
| 1H context + 15m trigger + confirmation | `+30` |
| 15m trigger + confirmation | `+20` |
| только 1H context | `+10` |
| 15m trigger без POI/SFP confirmation | `+5` |
| нет структуры | `0` |

### Liquidity

| Условие | Баллы |
| --- | --- |
| strong SFP | `+20` |
| medium SFP | `+10` |
| weak SFP | `+5` |
| нет SFP | `0` |

### Premium / Discount

| Условие | Результат |
| --- | --- |
| BUY в discount | разрешено |
| BUY в premium | `Score 0`, `Ignore` |
| SELL в premium | разрешено |
| SELL в discount | `Score 0`, `Ignore` |
| equilibrium | `Score 0`, `Ignore` |

### FVG

| Условие | Баллы |
| --- | --- |
| FVG Q90+ тестировался и зона удержана | `+15` |
| FVG Q75-89 тестировался и зона удержана | `+10` |
| FVG Q60-74 тестировался и зона удержана | `+5` |
| FVG ниже Q60 | `0` |
| FVG пробит / invalidated | `0` |
| FVG не тестировался | `0` |

Важно:

```text
Цена не обязана прямо сейчас быть внутри FVG.
Если был свежий ретест, зона удержана и quality tier достаточный,
FVG может дать баллы как часть сценария.
```

### Volume

| Условие | Баллы |
| --- | --- |
| объем на strong SFP | `+10` |
| объем на BOS/CHoCH с confirmation | `+10` |
| объем на экстремальном BOS без confirmation | `+5` |
| объем есть, но сигнал слабый | `0` |

### Macro

Макро дает дополнительные баллы, если DXY/SPX/BTC.D подтверждают направление.

Важно:

```text
Макро не создает сделку само по себе.
Price Action first, Macro second.
```

DXY/SPX теперь оцениваются не по одному последнему закрытию, а по 5-дневному bias:

```text
цена относительно EMA5
изменение за несколько дней
neutral threshold отдельно для DXY и SPX
```

Если данные DXY/SPX устарели или недоступны:

```text
0
```

BTC.D:

```text
BTC.D unavailable -> N/A, macro 0
BTC.D высокий для альтов -> 0, риск оттока ликвидности
```

Градация:

| Фон | Баллы |
| --- | --- |
| clean risk-on для long | `+10` |
| clean risk-off для short | `+10` |
| частичная поддержка, один из DXY/SPX neutral | `+5` |
| mixed / stale / unavailable | `0` |

Пример mixed:

```text
DXY bearish поддерживает риск,
SPX bearish давит на риск.
Макро: 0 (смешанный фон)
```

## 18. Решение по итоговому score

У score есть два слоя:

```text
raw_score = сумма всех найденных факторов до финальных gate/cap.
total_score = итоговый score после P/D, Scenario Gate, shallow P/D cap и ограничения до 100.
```

Почему так:

```text
Факторы теоретически могут набрать больше 100.
В отчете score показывается как /100, поэтому total_score ограничивается 100.
```

| Score | Решение |
| --- | --- |
| `>= 70` | `A+` |
| `>= 40` | `Watchlist` |
| `< 40` | `Ignore` |

### Scenario Gate

Высокая сумма баллов сама по себе больше не делает A+.

Для A+ нужен минимальный сценарный каркас:

```text
15m trigger есть
trigger подтвержден POI или SFP
есть FVG test или SFP context
```

Если score высокий, но сценарный каркас не собран:

```text
total_score = 69
decision = Watchlist
Scenario: WATCHLIST (score 90->69: нет обязательного Scenario Gate для A+)
```

Это защищает от ситуации:

```text
много отдельных плюсов,
но правильной Sniper-последовательности еще нет.
```

Важно:

```text
Внутри Kill Zone A+ может стать полноценным alert.
Вне Kill Zone score >= 85 показывается как A+ WATCH ONLY.
Это наблюдение, а не сигнал на сделку.
```

### Kill Zone / Session

Сессия считается через timezone:

```text
Europe/Chisinau
```

Больше нет ручного `UTC+3`, поэтому переход лето/зима учитывается автоматически.

Kill Zone окна:

| Session | Время |
| --- | --- |
| London KZ | `10:00 <= t < 12:00` |
| New York KZ | `15:30 <= t < 18:00` |

Граница окончания не включается:

```text
12:00 уже Outside KZ
18:00 уже Outside KZ
```

Сессия теперь объект:

```text
SessionResult(
    in_kill_zone=True,
    session_name="New York",
    local_time="16:15",
    timezone="Europe/Chisinau",
    minutes_to_session_end=105
)
```

В отчете:

```text
Session: New York KZ (105m left)
Session: ВНЕ KILL ZONE (next 30m)
```

Для настройки можно включить диагностику вне KZ:

```text
SEND_DIAGNOSTIC_OUTSIDE_KZ=true
```

Тогда HUNT dashboard будет приходить и вне Kill Zone, но A+ alert все равно не отправляется вне KZ.

## 19. Как бот думает по шагам

### Данные

```text
4H берется напрямую с биржи.
1H берется напрямую с биржи.
15m берется напрямую с биржи.
Последняя незакрытая свеча каждого таймфрейма отбрасывается.
```

Зачем:

```text
1H context должен совпадать с реальной биржевой 1H свечой,
а не собираться из 15m вручную.
```

### Шаг 1. Проверить Market State

```text
Если Neutral -> Score 0, сделки нет.
```

### Шаг 2. Проверить 4H Trend

```text
4H Bias: цена выше/ниже EMA99.
4H Structure: HH/HL, LH/LL или Neutral.
ADX: сила тренда и направление +DI/-DI.
```

### Шаг 3. Найти структуру

```text
1H = контекст
15m = триггер
BOS/CHoCH должны иметь нормальный displacement
```

### Шаг 4. Найти SFP

```text
Был ли sweep liquidity?
Насколько сильный?
Был ли уровень свежим, а не уже swept?
```

### Шаг 5. Проверить Liquidity Map

```text
Где ближайший buy-side pool?
Где ближайший sell-side pool?
Какие уровни internal, а какие external?
Не снят ли уже уровень?
```

### Шаг 6. Проверить Premium / Discount

```text
BUY находится в discount?
SELL находится в premium?
Цена не застряла около equilibrium?
```

Если ответ плохой, сетап блокируется.

### Шаг 7. Найти FVG

```text
Есть ли валидная зона?
Не заполнена ли она?
Был ли ретест?
```

### Шаг 8. Проверить объем

```text
Есть ли RVOL >= 1.5?
Но объем засчитывается только если сигнал качественный.
```

### Шаг 9. Проверить макро

```text
DXY/SPX/BTC.D помогают или мешают?
Данные свежие?
Фон clean, partial или mixed?
```

### Шаг 10. Проверить State Machine

```text
События произошли в правильном порядке?
Нет ли таймаута?
Совпадает ли направление?
```

Если сценарий invalidated, сигнал запрещен.

Если score высокий, но State Machine еще не `signal_ready`:

```text
total_score = 69
decision = Watchlist
Scenario: WATCHLIST (State Machine gate: waiting_for_bos ...)
```

### Шаг 11. Проверить Scenario Gate

```text
Есть ли trigger?
Есть ли POI/SFP confirmation?
Есть ли FVG/SFP context?
```

Без этого A+ запрещен, даже если raw_score высокий.

### Шаг 12. Сложить score

```text
Мало факторов -> Ignore.
Набор факторов -> Watchlist.
Сильная связка факторов -> A+.
```

## 20. Примеры из отчетов

### Сильный SFP, но без структуры

```text
Структура: 0
Ликвидность: +20 (SFP Q84 D0.46 R99)
Объем: +10
Score: 40
```

Что это значит:

```text
Ликвидность хорошая, но структуры нет.
Это еще не полноценный сетап.
```

### Сильный BOS без confirmation

```text
Структура: +5 (15m BOS Q100 ..., без POI/SFP confirmation)
Объем: +5
```

Что это значит:

```text
Импульс есть.
Но нет хорошего места входа.
Бот не дает много баллов.
```

### BOS in POI

```text
Структура: +20 (15m BOS Q81 ..., in POI)
Объем: +10
```

Что это значит:

```text
Есть структурный триггер в зоне интереса.
Это намного сильнее обычного BOS.
```

### Neutral

```text
Сетап: NEUTRAL
Score: 0
Структура: 0 (Neutral market state)
```

Что это значит:

```text
Рынок мутный.
Бот не ищет сделку.
```

### BUY в premium

```text
Тренд: +25
Ликвидность: +20
P/D: BLOCK (premium +10.00% от EQ, 25.00% range, score 45->0)
Score: 0
Decision: Ignore
```

Что это значит:

```text
Даже если есть какой-то сигнал,
бот не хочет покупать высоко в диапазоне.
Сначала он считает сырой score,
а потом P/D BLOCK обнуляет итоговый score.
```

### Нарушен порядок сценария

```text
FVG появился до CHoCH.
State Machine: INVALIDATED.
```

Что это значит:

```text
Даже если FVG качественный,
это не правильный Sniper-сценарий.
```

## 21. Простое правило для чтения графика

Перед тем как думать о сделке, спроси:

```text
1. Рынок вообще directional или Neutral?
2. Есть ли структура, а не просто случайная свеча?
3. Есть ли нормальный displacement?
4. Где находится свежая ликвидность?
5. Цена в правильной зоне P/D?
6. Был ли sweep liquidity?
7. Есть ли POI/FVG?
8. Есть ли объем именно на качественном сигнале?
9. События идут в правильном порядке?
10. Не против ли макро?
```

Если ответов мало, это не сетап.

## 22. Risk Plan

`RiskPlan` отвечает на вопрос:

```text
Даже если сетап найден, есть ли хорошая сделка по риску?
```

Это отдельный слой после score и State Machine.

### Что считает RiskPlan

| Поле | Что означает |
| --- | --- |
| `entry` | предполагаемая цена входа |
| `stop_loss` | стоп |
| `invalidation_level` | уровень, который ломает идею сделки |
| `target_1` | первая логичная цель |
| `target_2` | вторая цель, если есть |
| `rr_to_target_1` | RR до первой цели |
| `rr_to_target_2` | RR до второй цели |
| `entry_model` | откуда взят вход |
| `stop_model` | почему стоп стоит именно там |
| `target_model` | почему цель выбрана именно там |
| `late_entry` | не поздно ли входить |
| `valid` | можно ли дать A+ по риску |

### Entry

Приоритет входа:

```text
1. FVG midpoint
2. confirmation close, если цена еще рядом с FVG/POI
3. reclaim level после SFP
4. structure level fallback
```

Важно:

```text
BOS level больше не является основным entry по умолчанию.
Это только fallback.
```

### Stop Loss

Стоп теперь старается быть структурным:

Для LONG:

```text
ниже sweep level / FVG bottom / structural invalidation
```

Для SHORT:

```text
выше sweep level / FVG top / structural invalidation
```

ATR используется как buffer, а не как единственная логика стопа.

### Take Profit

Цель берется из Liquidity Map:

Для LONG:

```text
nearest buy-side liquidity
strongest buy-side liquidity
```

Для SHORT:

```text
nearest sell-side liquidity
strongest sell-side liquidity
```

Если реальной liquidity target нет, `3R fallback` может быть рассчитан, но A+ блокируется:

```text
no logical liquidity target
```

### RR-фильтр

| Условие | Решение |
| --- | --- |
| `RR < 1.5` | RiskPlan invalid |
| `1.5 <= RR < 2.0` | максимум Watchlist |
| `RR >= 2.0` | риск допустим |

### Late Entry

Если цена уже ушла далеко от POI/FVG:

```text
late entry: price moved too far from POI
```

Такой сетап не должен уходить как A+.

### В отчете

Пример:

```text
🛡 Risk: OK (fvg_midpoint -> nearest_liquidity, T1 2.40R / T2 3.80R, SL 1.20%, Risk plan valid)
```

Или:

```text
🛡 Risk: WATCHLIST (RR to target 1 below minimum, score 92->69, T1 1.00R)
```

Главное правило:

```text
Score показывает качество сетапа.
RiskPlan показывает качество сделки.
```

## 23. Research / Journal / Backtesting

Система теперь умеет сохранять не только сигналы, но и все сканы.

Главная идея:

```text
Telegram = для человека.
Journal = для исследования и статистики.
```

### Scan Journal

Каждый scan по каждой монете сохраняется в JSONL:

```text
data/journal/scans_YYYY-MM-DD.jsonl
```

Одна строка = один symbol scan.

В журнал попадает:

| Блок | Что сохраняется |
| --- | --- |
| `trend_4h` | EMA bias, ADX, +DI / -DI |
| `market_structure_4h` | trend, confidence, reason |
| `context_1h` | BOS/CHoCH context event |
| `trigger_15m` | BOS/CHoCH trigger event |
| `sfp` | sweep / SFP параметры |
| `premium_discount` | zone, depth, range |
| `liquidity_map` | nearest/strongest BSL/SSL |
| `risk_plan` | entry, SL, TP, RR, valid/reason |
| `diagnostics` | gates and no_trade_reason |
| `breakdown` | score components |

Журнал включен по умолчанию:

```bash
SCAN_JOURNAL_ENABLED=true
```

Отключить:

```bash
SCAN_JOURNAL_ENABLED=false
```

Для Docker путь журнала проброшен volume:

```text
./data:/app/data
```

После изменения `docker-compose.yml` нужно пересоздать контейнеры:

```bash
docker-compose up -d --build
```

Важно:

```text
/scan пишет scan journal.
/alerts использует старый daily_alerts.py и не является research scan.
```

### Смотреть summary журнала

```bash
.venv/bin/python research/analyze_journal.py
```

Или конкретный файл:

```bash
.venv/bin/python research/analyze_journal.py data/journal/scans_2026-07-10.jsonl
```

Скрипт покажет:

```text
сколько строк
какие symbols
decision_counts
no_trade_reason_counts
средний score
максимальный score
score_by_symbol
trend / neutral причины
1H context и 15m trigger quality
SFP strong/weak статистику
P/D zone и shallow/deep статистику
risk_plan valid/reason/RR
state_machine_counts
gates pass/fail
```

Как читать этот отчет:

| Поле | Что означает |
| --- | --- |
| `no_trade_reason_counts` | главные причины, почему бот не дает сделку |
| `features.market_structure_4h.trend_counts` | сколько раз 4H был bullish/bearish/neutral |
| `features.context_1h.detected` | сколько 1H BOS/CHoCH контекстов найдено |
| `features.trigger_15m.detected` | сколько 15m триггеров найдено |
| `features.sfp.strong_tier` | сколько SFP прошли strong-tier |
| `features.premium_discount.pd_valid_counts` | сколько сетапов прошли P/D фильтр |
| `features.risk_plan.valid_counts` | сколько сделок имели нормальный entry/SL/TP/RR |
| `gates.scenario_valid` | сколько сетапов прошли обязательную последовательность Sniper |
| `state_machine_counts` | на каком этапе чаще всего ломается сценарий |

Если почти все `Ignore`, сначала смотри:

```text
no_trade_reason_counts
gates
state_machine_counts
features.market_structure_4h.reason_counts
features.risk_plan.reason_counts
```

Например:

```text
scenario_valid=false 30/30
trigger_confirmed=false 30/30
neutral_htf 21/30
```

Это значит, что рынок может давать отдельные элементы,
но полная последовательность Sniper еще не собрана.

### Чистая snapshot-функция

Для backtest добавлена чистая функция:

```python
analyze_symbol_snapshot(symbol, df_4h, df_1h, df_15m, macro_data)
```

Она:

```text
не отправляет Telegram
не вызывает Gemini
не использует глобальный last_alert_time
не фетчит свечи сама
возвращает score_result и analysis_data
```

`prepare_and_analyze()` осталась live-wrapper функцией:

```text
fetch candles -> analyze_symbol_snapshot(...)
```

### Trade Simulator

Добавлен простой simulator:

```python
simulate_trade(
    candles,
    direction,
    entry,
    stop_loss,
    target_1,
    target_2=None,
    max_bars=96,
)
```

Правила:

| Ситуация | Итог |
| --- | --- |
| TP раньше SL | win |
| SL раньше TP | loss |
| SL и TP в одной свече | conservative: SL first |
| ни SL, ни TP до timeout | close at market |

Simulator считает:

```text
gross_R
net_R
MAE
MFE
bars_held
exit_reason
```

Важно:

```text
Это еще не полноценный годовой backtest.
Это фундамент для paper research и будущего historical backtest.
```

## 24. Тесты и валидация

Тесты разделены по смыслу:

```text
tests/
```

Это автоматические unit/integration тесты торговой логики.

```text
tools/
```

Это ручные диагностические скрипты.
Они могут ходить в сеть, требовать ключи и тратить квоты.
Их нельзя запускать как обычный test suite.

### Что запускать для регрессии

```bash
.venv/bin/python -m unittest discover tests
```

Если в окружении установлен pytest:

```bash
.venv/bin/python -m pytest
```

`pytest.ini` ограничивает discovery папкой `tests`.

### Что НЕ является unit-тестом

Эти файлы вынесены из `tests/`:

| Файл | Почему вынесен |
| --- | --- |
| `tools/check_gemini_models.py` | требует Gemini API |
| `tools/check_gemini_models_stress.py` | делает реальные API-запросы и может тратить квоту |
| `tools/run_score_engine_test.py` | live-полигон с реальными market data |
| `tools/run_smc_engine_test.py` | live-полигон с реальными market data |

### Новые regression guards

| Тест | Что защищает |
| --- | --- |
| look-ahead swing test | swing внутри `right_bars` не используется для BOS |
| direct 1H RVOL test | 1H RVOL считается по прямым 1H свечам, не из 15m RVOL |
| State Machine gate test | высокий score не становится A+, если сценарий не завершен |
| FVG retest without displacement | retest FVG без displacement не готовит сигнал |

Главный принцип тестов:

```text
Правильный порядок событий -> сигнал может стать готовым.
Нарушенный порядок или неполная цепочка -> NO TRADE / Watchlist.
```

## 25. Самая короткая версия

```text
Neutral = не торгуем.
BOS = продолжение структуры.
CHoCH = подтвержденная смена структуры.
SFP = снятие ликвидности и возврат.
FVG = зона imbalance.
Displacement = сила импульса.
Liquidity Map = карта стопов и пулов ликвидности.
Premium = верхняя половина range, лучше для SELL.
Discount = нижняя половина range, лучше для BUY.
Equilibrium = середина range, слабая зона входа.
State Machine = проверка правильной последовательности сценария.
RiskPlan = проверка entry / SL / TP / RR.
Journal = память всех решений системы.
Trade Simulator = проверка исхода сделки по будущим свечам.
Q = качество.
DR = сила импульса относительно ATR.
BR = чистота тела свечи.
D = глубина снятия ликвидности.
R = сила rejection.
RVOL = объем.
POI = место, где сигнал имеет смысл.
Swept = уровень уже сняли.
Internal = ликвидность внутри range.
External = ликвидность за пределами range.
```

И главное:

```text
Бот теперь не ищет "любой сигнал".
Он ищет комбинацию качественных причин.
```
