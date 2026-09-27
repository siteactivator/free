"""Каталог парсеров A-Parser 1.2.3656: итог проверки 27.09.2026 и какой командой скилла вызывается.

Итоги: ok — работает; part — частично; bad — не заработал с первой попытки (мировые прокси
Premium Mix, настройки по умолчанию); key — нужен аккаунт или ключ; skip — не запускаем;
util — служебный. Таблица по разделам — PARSERS.md.
"""

STATUS = {"ok": "✅ работает", "part": "⚠️ частично", "bad": "❌ не заработал", "key": "🔑 нужен аккаунт/ключ",
          "skip": "⛔ не запускаем", "util": "⚙️ служебный"}

# парсер: (итог, команда скилла, заметка)
PARSERS = {
    # выдача
    "SE::Yandex": ("ok", "serp/positions/index --engine yandex", "только engine=browser; 27.09 — 6–7 мин на запрос"),
    "SE::Google": ("ok", "serp/positions --engine google", "адреса настоящие; регион — по IP прокси, язык — ru"),
    "SE::DuckDuckGo": ("ok", "serp/positions --engine duckduckgo", "регион ru-ru"),
    "SE::Yahoo": ("ok", "serp --engine yahoo", ""),
    "SE::AOL": ("ok", "serp --engine aol", ""),
    "SE::Rambler": ("ok", "serp --engine rambler", "~2,5 мин на запрос"),
    "SE::Seznam": ("ok", "serp --engine seznam", "чешский поисковик"),
    "SE::You": ("ok", "serp --engine you", "ИИ-ответ часто пустой"),
    "SE::Pinterest": ("ok", "images --engine pinterest", ""),
    "SE::Wikipedia": ("ok", "wiki", ""),
    "SE::Wikipedia::Article": ("ok", "wiki (адрес статьи)", ""),
    "SE::YouTube": ("ok", "video --engine youtube", ""),
    "SE::YouTube::Video": ("ok", "youtube", "с комментариями — до 8 мин"),
    "SE::Bing": ("bad", "stub bing", "пусто за 7 мин; выдача Bing — через SE::Bing::Position (serp --engine bing)"),
    "SE::Baidu": ("bad", "stub baidu", ""), "SE::Brave": ("bad", "stub brave", ""), "SE::Dogpile": ("bad", "stub dogpile", ""), "SE::Startpage": ("bad", "stub startpage", ""),
    "SE::Quora": ("key", "stub quora", "кука аккаунта Quora"),
    # позиции
    "SE::Google::Position": ("ok", "raw", "точный хост (Match type: Exact domain); в скилле позиции считаются по выдаче"),
    "SE::Bing::Position": ("ok", "serp/positions --engine bing", "используется как источник выдачи Bing"),
    "SE::DuckDuckGo::Position": ("ok", "raw", "точный хост"),
    "SE::Yandex::Position": ("bad", "stub yandex-position", "http — пусто, browser — завис на 38 мин; позиции — positions --engine yandex"),
    # подсказки
    "SE::Yandex::Suggest": ("ok", "suggest --engine yandex", ""), "SE::Google::Suggest": ("ok", "suggest --engine google", ""),
    "SE::Bing::Suggest": ("ok", "suggest --engine bing", ""), "SE::Yahoo::Suggest": ("ok", "suggest --engine yahoo", ""),
    "SE::YouTube::Suggest": ("ok", "suggest --engine youtube", ""), "SE::Pinterest::Suggest": ("ok", "suggest --engine pinterest", ""),
    "SE::Google::Trends::Suggest": ("ok", "suggest --engine trends", ""), "SE::AOL::Suggest": ("bad", "stub aol-suggest", ""),
    # ключевые слова
    "SE::Google::Trends": ("ok", "trends", "только мир и 5 лет: фильтр страны его ломает"),
    "Rank::Ahrefs::KeywordGenerator": ("ok", "keywords --source ahrefs", "сложность словом, частота диапазоном"),
    "Rank::Ahrefs::KeywordDifficulty": ("ok", "kd", ""),
    "Rank::Bukvarix::Keyword": ("ok", "keywords --source bukvarix", "бесплатный ключ, до 1000 фраз"),
    "Rank::Bukvarix::Domain": ("ok", "domain-keywords", ""),
    "SE::Yandex::Direct": ("ok", "ads", ""),
    "SE::Yandex::WordStat": ("skip", "", "риск для аккаунта; частотности — официальный API Wordstat"),
    "SE::Yandex::WordStat::ByDate": ("skip", "", "как WordStat"), "SE::Yandex::WordStat::ByRegion": ("skip", "", "как WordStat"),
    "SE::Yandex::WordCraft": ("key", "stub wordcraft", "аккаунты Яндекса в <папка A-Parser>\\files\\SE-Yandex\\accounts.txt"),
    "SE::Google::KeywordPlanner::Ideas": ("key", "stub kp-ideas", "аккаунт Google Ads"),
    "SE::Google::KeywordPlanner::SearchVolume": ("key", "stub kp-volume", "аккаунт Google Ads"),
    "Rank::KeysSo": ("key", "stub keysso", "кука userlogin из браузера после входа в keys.so; токен API не подходит — он для официального API Keys.so"),
    # домены и ссылки
    "Rank::Ahrefs": ("ok", "ahrefs, domains --checks ahrefs", ""),
    "Rank::Ahrefs::BrokenLinks": ("ok", "broken", ""),
    "Rank::Ahrefs::TrafficChecker": ("ok", "traffic, domains --checks traffic", ""),
    "Rank::MajesticSEO": ("ok", "domains --checks majestic", "TF/CF"),
    "Rank::Mustat": ("ok", "domains --checks mustat", ""),
    "Rank::MOZ": ("ok", "moz, domains --checks moz", ""),
    "Rank::Archive": ("ok", "domains --checks archive", ""), "Rank::CMS": ("ok", "domains --checks cms", ""),
    "Rank::Curlie": ("ok", "domains --checks curlie", ""), "Rank::Social::Signal": ("ok", "domains --checks social", ""),
    "Cloudflare::Radar": ("ok", "domains --checks radar", ""),
    "Check::RosKomNadzor": ("ok", "domains --checks rkn_ap", "обычно хватает реестра antifilter (--checks rkn)"),
    "Check::BackLink": ("ok", "backlink", ""),
    "Net::Whois": ("ok", "domains --checks whois", ""), "Net::DNS": ("ok", "domains --checks dns", ""),
    "Net::HTTP": ("ok", "http", ""), "IP::Geo": ("ok", "ip", ""), "IP::Info": ("ok", "ip, domains --checks hosting", ""),
    "SE::Google::SafeBrowsing": ("ok", "domains --checks safe_google", ""),
    "SE::Yandex::SafeBrowsing": ("ok", "domains --checks safe_yandex", ""),
    "SE::Google::Compromised": ("ok", "domains --checks hacked", "Google — до 5 мин на домен"),
    "SE::Yandex::SQI": ("ok", "domains --checks sqi", ""),
    "SecurityTrails::Domain": ("ok", "trails, domains --checks trails", "без логина — текущие записи"),
    "SecurityTrails::IP": ("bad", "stub trails-ip", ""),
    "SE::Google::TrustCheck": ("part", "stub trustcheck", "траст 0 даже у Википедии — не верить"),
    # страницы
    "Browser::ScreenshotsMaker": ("ok", "screenshot", ""), "HTML::ArticleExtractor": ("ok", "extract --what article", ""),
    "HTML::LinkExtractor": ("ok", "extract --what links", ""), "HTML::TextExtractor": ("ok", "extract --what text", ""),
    "HTML::TextExtractor::LangDetect": ("ok", "extract --what lang", ""),
    "HTML::EmailExtractor": ("skip", "", "сбор email"), "SEO::Ping": ("skip", "", "рассылка пингов"),
    # картинки и видео
    "SE::Yandex::Images": ("ok", "images --engine yandex", ""), "SE::Yandex::Video": ("ok", "video --engine yandex", ""),
    "SE::Yandex::ByImage": ("ok", "byimage", "JPG; PNG-миниатюру не взял"), "SE::DuckDuckGo::Images": ("ok", "images --engine duckduckgo", ""),
    "SE::Bing::Images": ("part", "stub bing-images", "1 результат"), "SE::Bing::Video": ("part", "stub bing-video", "0 результатов"),
    "SE::Google::Images": ("bad", "stub google-images", ""), "SE::Google::ByImage": ("bad", "stub google-byimage", ""), "SE::Dogpile::Images": ("bad", "stub dogpile-images", ""),
    "SE::Startpage::Images": ("bad", "stub startpage-images", ""), "SE::Startpage::Videos": ("bad", "stub startpage-videos", ""),
    # переводы и тексты
    "SE::Google::Translate": ("ok", "translate --engine google", ""), "SE::Bing::Translator": ("ok", "translate --engine bing", ""),
    "DeepL::Translator": ("ok", "translate --engine deepl", ""), "DeepL::Write": ("ok", "proofread", ""),
    "SE::Yandex::Speller": ("ok", "speller", ""), "SE::Yandex::Translate": ("bad", "stub yandex-translate", "13 мин без ответа"),
    "SE::Yandex::Balaboba": ("bad", "", "сервис закрыт Яндексом"),
    # нейросети
    "FreeAI::GoogleAI": ("ok", "ai --engine googleai", "до 7 мин"), "FreeAI::DuckAI": ("ok", "ai --engine duckai", ""),
    "FreeAI::DeepAI": ("ok", "ai --engine deepai", ""),
    "FreeAI::ChatGPT": ("bad", "stub chatgpt-free", ""), "FreeAI::Copilot": ("bad", "stub copilot", ""), "FreeAI::Perplexity": ("bad", "stub perplexity", "без куки аккаунта"),
    "FreeAI::Kimi": ("key", "stub kimi", "токен kimi.com"), "OpenAI::ChatGPT": ("ok", "ai --engine openai", "ключ OPENAI_API_KEY в .env; gpt-5-mini по умолчанию"),
    "OpenAI::Completions": ("ok", "raw --parser OpenAI::Completions --set model=gpt-3.5-turbo-instruct", "устаревший API дополнений; ключ подставляется сам"),
    "FreeAI::Server::OpenAI": ("util", "", "локальный OpenAI-совместимый сервер"),
    # магазины и приложения
    "AppStore::Apps": ("ok", "apps --store appstore", ""), "GooglePlay::Apps": ("ok", "apps --store googleplay", ""),
    "Shop::Amazon": ("ok", "shop --store amazon", ""), "Shop::AliExpress": ("ok", "shop --store aliexpress", ""),
    "Shop::Yandex::Market": ("ok", "shop --store market", ""), "Shop::eBay": ("bad", "stub ebay", ""),
    "Shop::Wildberries::ProductsList": ("bad", "stub wb-search", "возможно, нужны российские прокси"), "Shop::Wildberries::Suggest": ("bad", "stub wb-suggest", ""),
    "Shop::Wildberries::ProductInfo": ("bad", "stub wb-product", "не проверен: ссылку брали из ProductsList"),
    # карты
    "Maps::Google": ("ok", "maps --engine google", ""), "Maps::Yandex": ("ok", "maps --engine yandex", ""),
    "Maps::Google::Reviews": ("part", "stub google-reviews", "рейтинг и число отзывов есть, самих отзывов — первые 5"),
    # соцсети
    "Reddit::Posts": ("ok", "reddit", ""), "Reddit::PostInfo": ("ok", "reddit (адрес поста)", ""), "Reddit::Comments": ("bad", "stub reddit-comments", ""),
    "Social::TikTok::Profile": ("ok", "tiktok", "без списков подписчиков"), "Telegram::GroupScraper": ("ok", "telegram", ""),
    "Social::Instagram::Geo": ("key", "stub instagram-geo", "кука Instagram"), "Social::Instagram::Post": ("key", "stub instagram-post", "кука Instagram"),
    "Social::Instagram::Profile": ("key", "stub instagram-profile", "кука Instagram"), "Social::Instagram::Search": ("key", "stub instagram-search", "кука Instagram"),
    "Social::Instagram::Tag": ("key", "stub instagram-tag", "кука Instagram"),
    # прочее и служебные
    "CoinMarketCap::LastPrice": ("ok", "crypto", ""),
    "Util::AntiGate": ("ok", "(внутри Яндекса и др.)", "пресет capmonster"), "Util::ReCaptcha2": ("ok", "(внутри Google)", "пресет capmonster"),
    "Util::Turnstile": ("ok", "(внутри Ahrefs, Majestic, Bing)", "пресет capmonster"),
    "Util::ReCaptcha3": ("util", "", ""), "Util::RotateCaptcha": ("util", "", ""), "Util::hCaptcha": ("util", "", ""),
    "Util::SMS": ("util", "", ""), "Util::YandexRecognize": ("util", "", ""), "API::Server::Redis": ("util", "", ""),
    "SE::Yandex::Register": ("skip", "", "массовая регистрация аккаунтов"),
}
