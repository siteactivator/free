# Парсеры A-Parser 1.2.3656: что работает и какой командой скилла

Проверка 27.09.2026: каждый парсер — одним запросом, через мировые прокси Premium Mix, капча —
CapMonster, настройки по умолчанию. ❌ значит «не заработало с первой попытки у нас», а не
окончательный приговор: для таких парсеров есть заготовки `python run.py stub <имя>` — метод
добавлен, но в работе не проверен, разбирайтесь сами (README, раздел про заготовки).

Итог: ✅ работает — 86, ⚠️ частично — 4, ❌ не заработал — 23, 🔑 нужен аккаунт/ключ — 11, ⛔ не запускаем — 6, ⚙️ служебный — 7.

Та же таблица в терминале: `python run.py parsers` (или `--status ok|part|bad|key|skip|util`).

## ✅ работает — 86

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `AppStore::Apps` | `apps --store appstore` |  |
| `Browser::ScreenshotsMaker` | `screenshot` |  |
| `Check::BackLink` | `backlink` |  |
| `Check::RosKomNadzor` | `domains --checks rkn_ap` | обычно хватает реестра antifilter (--checks rkn) |
| `Cloudflare::Radar` | `domains --checks radar` |  |
| `CoinMarketCap::LastPrice` | `crypto` |  |
| `DeepL::Translator` | `translate --engine deepl` |  |
| `DeepL::Write` | `proofread` |  |
| `FreeAI::DeepAI` | `ai --engine deepai` |  |
| `FreeAI::DuckAI` | `ai --engine duckai` |  |
| `FreeAI::GoogleAI` | `ai --engine googleai` | до 7 мин |
| `GooglePlay::Apps` | `apps --store googleplay` |  |
| `HTML::ArticleExtractor` | `extract --what article` |  |
| `HTML::LinkExtractor` | `extract --what links` |  |
| `HTML::TextExtractor` | `extract --what text` |  |
| `HTML::TextExtractor::LangDetect` | `extract --what lang` |  |
| `IP::Geo` | `ip` |  |
| `IP::Info` | `ip, domains --checks hosting` |  |
| `Maps::Google` | `maps --engine google` |  |
| `Maps::Yandex` | `maps --engine yandex` |  |
| `Net::DNS` | `domains --checks dns` |  |
| `Net::HTTP` | `http` |  |
| `Net::Whois` | `domains --checks whois` |  |
| `OpenAI::ChatGPT` | `ai --engine openai` | ключ OPENAI_API_KEY в .env; gpt-5-mini по умолчанию |
| `OpenAI::Completions` | `raw --parser OpenAI::Completions --set model=gpt-3.5-turbo-instruct` | устаревший API дополнений; ключ подставляется сам |
| `Rank::Ahrefs` | `ahrefs, domains --checks ahrefs` |  |
| `Rank::Ahrefs::BrokenLinks` | `broken` |  |
| `Rank::Ahrefs::KeywordDifficulty` | `kd` |  |
| `Rank::Ahrefs::KeywordGenerator` | `keywords --source ahrefs` | сложность словом, частота диапазоном |
| `Rank::Ahrefs::TrafficChecker` | `traffic, domains --checks traffic` |  |
| `Rank::Archive` | `domains --checks archive` |  |
| `Rank::Bukvarix::Domain` | `domain-keywords` |  |
| `Rank::Bukvarix::Keyword` | `keywords --source bukvarix` | бесплатный ключ, до 1000 фраз |
| `Rank::CMS` | `domains --checks cms` |  |
| `Rank::Curlie` | `domains --checks curlie` |  |
| `Rank::MOZ` | `moz, domains --checks moz` |  |
| `Rank::MajesticSEO` | `domains --checks majestic` | TF/CF |
| `Rank::Mustat` | `domains --checks mustat` |  |
| `Rank::Social::Signal` | `domains --checks social` |  |
| `Reddit::PostInfo` | `reddit (адрес поста)` |  |
| `Reddit::Posts` | `reddit` |  |
| `SE::AOL` | `serp --engine aol` |  |
| `SE::Bing::Position` | `serp/positions --engine bing` | используется как источник выдачи Bing |
| `SE::Bing::Suggest` | `suggest --engine bing` |  |
| `SE::Bing::Translator` | `translate --engine bing` |  |
| `SE::DuckDuckGo` | `serp/positions --engine duckduckgo` | регион ru-ru |
| `SE::DuckDuckGo::Images` | `images --engine duckduckgo` |  |
| `SE::DuckDuckGo::Position` | `raw` | точный хост |
| `SE::Google` | `serp/positions --engine google` | адреса настоящие; регион — по IP прокси, язык — ru |
| `SE::Google::Compromised` | `domains --checks hacked` | Google — до 5 мин на домен |
| `SE::Google::Position` | `raw` | точный хост (Match type: Exact domain); в скилле позиции считаются по выдаче |
| `SE::Google::SafeBrowsing` | `domains --checks safe_google` |  |
| `SE::Google::Suggest` | `suggest --engine google` |  |
| `SE::Google::Translate` | `translate --engine google` |  |
| `SE::Google::Trends` | `trends` | только мир и 5 лет: фильтр страны его ломает |
| `SE::Google::Trends::Suggest` | `suggest --engine trends` |  |
| `SE::Pinterest` | `images --engine pinterest` |  |
| `SE::Pinterest::Suggest` | `suggest --engine pinterest` |  |
| `SE::Rambler` | `serp --engine rambler` | ~2,5 мин на запрос |
| `SE::Seznam` | `serp --engine seznam` | чешский поисковик |
| `SE::Wikipedia` | `wiki` |  |
| `SE::Wikipedia::Article` | `wiki (адрес статьи)` |  |
| `SE::Yahoo` | `serp --engine yahoo` |  |
| `SE::Yahoo::Suggest` | `suggest --engine yahoo` |  |
| `SE::Yandex` | `serp/positions/index --engine yandex` | только engine=browser; 27.09 — 6–7 мин на запрос |
| `SE::Yandex::ByImage` | `byimage` | JPG; PNG-миниатюру не взял |
| `SE::Yandex::Direct` | `ads` |  |
| `SE::Yandex::Images` | `images --engine yandex` |  |
| `SE::Yandex::SQI` | `domains --checks sqi` |  |
| `SE::Yandex::SafeBrowsing` | `domains --checks safe_yandex` |  |
| `SE::Yandex::Speller` | `speller` |  |
| `SE::Yandex::Suggest` | `suggest --engine yandex` |  |
| `SE::Yandex::Video` | `video --engine yandex` |  |
| `SE::You` | `serp --engine you` | ИИ-ответ часто пустой |
| `SE::YouTube` | `video --engine youtube` |  |
| `SE::YouTube::Suggest` | `suggest --engine youtube` |  |
| `SE::YouTube::Video` | `youtube` | с комментариями — до 8 мин |
| `SecurityTrails::Domain` | `trails, domains --checks trails` | без логина — текущие записи |
| `Shop::AliExpress` | `shop --store aliexpress` |  |
| `Shop::Amazon` | `shop --store amazon` |  |
| `Shop::Yandex::Market` | `shop --store market` |  |
| `Social::TikTok::Profile` | `tiktok` | без списков подписчиков |
| `Telegram::GroupScraper` | `telegram` |  |
| `Util::AntiGate` | `(внутри Яндекса и др.)` | пресет capmonster |
| `Util::ReCaptcha2` | `(внутри Google)` | пресет capmonster |
| `Util::Turnstile` | `(внутри Ahrefs, Majestic, Bing)` | пресет capmonster |

## ⚠️ частично — 4

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `Maps::Google::Reviews` | `stub google-reviews` | рейтинг и число отзывов есть, самих отзывов — первые 5 |
| `SE::Bing::Images` | `stub bing-images` | 1 результат |
| `SE::Bing::Video` | `stub bing-video` | 0 результатов |
| `SE::Google::TrustCheck` | `stub trustcheck` | траст 0 даже у Википедии — не верить |

## ❌ не заработал — 23

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `FreeAI::ChatGPT` | `stub chatgpt-free` |  |
| `FreeAI::Copilot` | `stub copilot` |  |
| `FreeAI::Perplexity` | `stub perplexity` | без куки аккаунта |
| `Reddit::Comments` | `stub reddit-comments` |  |
| `SE::AOL::Suggest` | `stub aol-suggest` |  |
| `SE::Baidu` | `stub baidu` |  |
| `SE::Bing` | `stub bing` | пусто за 7 мин; выдача Bing — через SE::Bing::Position (serp --engine bing) |
| `SE::Brave` | `stub brave` |  |
| `SE::Dogpile` | `stub dogpile` |  |
| `SE::Dogpile::Images` | `stub dogpile-images` |  |
| `SE::Google::ByImage` | `stub google-byimage` |  |
| `SE::Google::Images` | `stub google-images` |  |
| `SE::Startpage` | `stub startpage` |  |
| `SE::Startpage::Images` | `stub startpage-images` |  |
| `SE::Startpage::Videos` | `stub startpage-videos` |  |
| `SE::Yandex::Balaboba` | — | сервис закрыт Яндексом |
| `SE::Yandex::Position` | `stub yandex-position` | http — пусто, browser — завис на 38 мин; позиции — positions --engine yandex |
| `SE::Yandex::Translate` | `stub yandex-translate` | 13 мин без ответа |
| `SecurityTrails::IP` | `stub trails-ip` |  |
| `Shop::Wildberries::ProductInfo` | `stub wb-product` | не проверен: ссылку брали из ProductsList |
| `Shop::Wildberries::ProductsList` | `stub wb-search` | возможно, нужны российские прокси |
| `Shop::Wildberries::Suggest` | `stub wb-suggest` |  |
| `Shop::eBay` | `stub ebay` |  |

## 🔑 нужен аккаунт/ключ — 11

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `FreeAI::Kimi` | `stub kimi` | токен kimi.com |
| `Rank::KeysSo` | `stub keysso` | кука userlogin из браузера после входа в keys.so; токен API не подходит — он для официального API Keys.so |
| `SE::Google::KeywordPlanner::Ideas` | `stub kp-ideas` | аккаунт Google Ads |
| `SE::Google::KeywordPlanner::SearchVolume` | `stub kp-volume` | аккаунт Google Ads |
| `SE::Quora` | `stub quora` | кука аккаунта Quora |
| `SE::Yandex::WordCraft` | `stub wordcraft` | аккаунты Яндекса в <папка A-Parser>\files\SE-Yandex\accounts.txt |
| `Social::Instagram::Geo` | `stub instagram-geo` | кука Instagram |
| `Social::Instagram::Post` | `stub instagram-post` | кука Instagram |
| `Social::Instagram::Profile` | `stub instagram-profile` | кука Instagram |
| `Social::Instagram::Search` | `stub instagram-search` | кука Instagram |
| `Social::Instagram::Tag` | `stub instagram-tag` | кука Instagram |

## ⛔ не запускаем — 6

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `HTML::EmailExtractor` | — | сбор email |
| `SE::Yandex::Register` | — | массовая регистрация аккаунтов |
| `SE::Yandex::WordStat` | — | риск для аккаунта; частотности — официальный API Wordstat |
| `SE::Yandex::WordStat::ByDate` | — | как WordStat |
| `SE::Yandex::WordStat::ByRegion` | — | как WordStat |
| `SEO::Ping` | — | рассылка пингов |

## ⚙️ служебный — 7

| Парсер | Команда скилла | Заметка |
|---|---|---|
| `API::Server::Redis` | — |  |
| `FreeAI::Server::OpenAI` | — | локальный OpenAI-совместимый сервер |
| `Util::ReCaptcha3` | — |  |
| `Util::RotateCaptcha` | — |  |
| `Util::SMS` | — |  |
| `Util::YandexRecognize` | — |  |
| `Util::hCaptcha` | — |  |
