# yandex-business-mcp

MCP-сервер и CLI для управления сетью филиалов в **Яндекс Бизнесе** (бывший Яндекс Справочник) из Claude Code или любого MCP-клиента.

Публичного API на запись у Яндекс Бизнеса нет. Официальный способ массово управлять данными сети — **XML-фид**: вы публикуете файл по постоянному URL, Яндекс его забирает, модерирует и применяет к карточкам. Этот инструмент делает фид управляемым из агента: данные филиалов живут в YAML, агент правит их через MCP, фид собирается и проверяется локально.

## Как это устроено

```
кабинет Яндекс Бизнеса ──(выгрузка XML)──▶ ybiz import ──▶ data/branches/*.yaml   ◀── агент правит через MCP
                                                  │
                                                  └──▶ data/baseline/yandex-export.xml (что сейчас живёт в Яндексе)

data/branches/*.yaml ──▶ ybiz build ──▶ feed/feed.xml ──(ваш хостинг, постоянный URL)──▶ Яндекс забирает фид
                            │
                            ├─ XSD Яндекса + правила из документации
                            └─ защита: не закроет филиалы из baseline без явного allow_close
```

Инструмент **ничего не отправляет в Яндекс сам**. Он меняет только файлы в workspace, а публикация фида и привязка URL в кабинете остаются за вами.

### Workspace — отдельно от кода

Код не знает ничего о конкретном клиенте. Данные сети лежат в **workspace**, это обычная папка (лучше приватный git-репо):

```
my-chain/
├── ybiz.yaml                        # chain_id, рубрики сети, пути
├── data/branches/<company-id>.yaml  # источник правды: один файл на филиал
├── data/baseline/yandex-export.xml  # последняя выгрузка из кабинета
└── feed/feed.xml                    # собранный фид
```

Путь к workspace задаётся переменной `YBIZ_WORKSPACE` (или аргументом `workspace` у инструментов, или `--workspace` у CLI).

### Формат филиала

```yaml
company-id: moscow_15            # неизменный id, задаёте вы (A-Z a-z 0-9 _ -, до 80 символов)
name: {ru: Ромашка}              # мультиязычные поля: язык -> строка или список строк
name-other: {ru: Допофис №1}
address: {ru: Москва, Ленинский проспект, 72к2}
country: {ru: Россия}
coordinates: {lon: '37.55', lat: '55.70'}
phone:
- {number: +7 (495) 123-45-67, type: phone}
email: [info@example.ru]         # email, add-url, rubric-id — всегда списки
url: https://example.ru
working-time: {ru: ежедн. 10:00-21:00}
rubric-id: ['184106414']
features:
- {feature: boolean, name: wi_fi, value: '1'}
photos:
  photo:
  - {url: https://example.ru/1.jpg, tags: [EXTERIOR]}
```

Допустимые ключи: `company-id, name, shortname, name-other, country, post-index, address, address-add, coordinates, phone, email, url, add-url, info-page, working-time, scheduled-working-time, rubric-id, chain-id, inn, ogrn, photos, features`. Неизвестные элементы из выгрузки не теряются: они сохраняются как есть под `_raw`.

`actualization-date` проставляется автоматически при сборке: у изменённых филиалов ставится сегодняшняя дата, у неизменённых остаётся прежняя.

## Установка

### Claude Code

```bash
claude mcp add yandex-business \
  --env YBIZ_WORKSPACE=/path/to/my-chain \
  -- uvx --from git+https://github.com/nikolaymokh-dev/yandex-business-mcp yandex-business-mcp
```

### `.mcp.json` в репо с данными сети

```jsonc
{
  "mcpServers": {
    "yandex-business": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/nikolaymokh-dev/yandex-business-mcp", "yandex-business-mcp"],
      "env": { "YBIZ_WORKSPACE": "." }
    }
  }
}
```

### CLI

```bash
uvx --from git+https://github.com/nikolaymokh-dev/yandex-business-mcp ybiz --help
```

## Инструменты MCP

| Инструмент | Что делает |
|---|---|
| `ybiz_status` | конфиг, число филиалов, есть ли baseline и фид |
| `ybiz_list_branches` | список филиалов (id, название, адрес, рубрики) |
| `ybiz_get_branch` | полные данные филиала |
| `ybiz_update_branch` | изменить поля; филиал с ошибками не сохраняется, `company-id` менять нельзя |
| `ybiz_add_branch` | новый филиал |
| `ybiz_import_export` | выгрузка из кабинета → YAML + baseline |
| `ybiz_validate` | XSD + правила Яндекса |
| `ybiz_diff` | added / **removed (будут закрыты)** / changed относительно baseline |
| `ybiz_build` | собрать `feed/feed.xml`; закрытие филиалов только через `allow_close` |

## Рабочий цикл

1. **Первый раз.** Откройте кабинет → сеть → «Автоматизация» → «Управление филиалами» → «Файл» → «Выгрузить данные». Затем `ybiz init` и `ybiz import export.xml`.
2. **Правка.** Попросите агента, например: «поменяй часы работы филиала moscow_15 на 9–22». Затем `ybiz_diff` и `ybiz_build`.
3. **Публикация.** Выложите `feed/feed.xml` по постоянному HTTPS-URL. Один раз укажите его в кабинете: «Автоматизация» → «Файл» → тип XML → «Проверить» → «Опубликовать».
4. **Перед каждой публикацией** делайте свежую выгрузку и `ybiz import --force` → `ybiz diff`, чтобы не затереть правки, сделанные в кабинете вручную.

## Важно про XML-фид Яндекса

- **Филиал, которого нет в фиде, Яндекс закрывает.** Поэтому `build` сверяется с baseline и не соберёт фид, в котором пропал живой филиал.
- `company-id` нельзя менять, пока филиал работает по адресу. При переезде нужен новый id, id закрытых филиалов нельзя переиспользовать.
- Модерация сверяет фид с сайтом: адрес, телефоны, основная рубрика и часы работы должны совпадать с сайтом сети.
- `name` — название сети без уточнений. Уточнения пишутся в `name-other`.
- Один фид — одна страна.
- Справочник рубрик: [rubric.xlsx](https://doc-static.yandex.net/src/support/business-priority/ru/rubric.xlsx). Доступные признаки для вашей рубрики: кабинет → «Автоматизация» → «Файл» → «Выгрузить признаки».

Документация Яндекса: [обновление данных через XML-файл](https://yandex.ru/support/business-priority/ru/branches/branches-xml). XSD-схема в `src/yandex_business_mcp/schema/partner-public.xsd` — копия [официальной](https://doc-static.yandex.net/src/support/business-priority/ru/partner-public.xsd).

## Чего здесь нет

Отзывы, ответы на отзывы, публикации, сторис и статистика через XML-фид недоступны, публичного API для них у Яндекса тоже нет. Их можно автоматизировать только через браузер на вашей сессии, это неофициальный путь и вне рамок этого пакета.

## Разработка

```bash
uv sync && uv run pytest -q
```

Лицензия MIT. Проект не связан с ООО «Яндекс».
