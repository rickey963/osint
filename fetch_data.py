"""
OSINT Dashboard data pipeline.

fetch (per source, concurrently) -> clean/trim -> dedupe within each section
(same event reported by multiple outlets collapses into one card, annotated
"potwierdzone przez N źródeł") -> translate to Polish -> derive map markers /
country instability / critical alerts / investment heuristic from the
collected text -> write data.json.

Only sources listed in scraper/sources.py are used - see that file's
docstring for why a couple of them go through Google News instead of a
native feed.
"""
import re
import json
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests
from deep_translator import GoogleTranslator

from scraper import sources

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_ARTICLES_PER_SOURCE = 30
MAX_ARTICLES_PER_SECTION = 50
FRESHNESS_WINDOW_HOURS = 72
DEDUPE_OVERLAP_THRESHOLD = 0.5

# Shared across all sections (which now fetch concurrently - see main()) so the
# total number of simultaneous Google Translate requests stays capped even
# though several sections are finalizing items at the same time. Without this
# cap, N sections each opening their own translate pool multiplies concurrent
# requests against Google's free endpoint and triggers connection resets.
TRANSLATE_EXECUTOR = ThreadPoolExecutor(max_workers=8)

# Sources whose native content is already Polish (see scraper/sources.py) -
# roughly two-thirds of all configured sources. Running these through Google
# Translate anyway would just be a slow PL->PL round trip for nothing, so
# _finalize() skips translation for them entirely. This is by far the
# biggest lever on total pipeline time, since translation (network calls to
# an unofficial endpoint) dominates the "Run data fetcher" step.
POLISH_SOURCES = frozenset({
    'Polsat News', 'Rzeczpospolita', 'Gazeta Wyborcza', 'Business Insider Polska',
    'PAP', 'Polskie Radio 24',
    "Spider's Web", 'Instalki.pl', 'Antyweb', 'Benchmark.pl',
    'Zaufana Trzecia Strona', 'Niebezpiecznik', 'Sekurak', 'CyberDefence24',
    'Money.pl', 'Bankier.pl', 'Forbes Polska', '300Gospodarka',
    'Inwestomat (Albert Rokicki)', 'Strefa Inwestorów', 'Forsal.pl',
    'Biznesradar', 'StockWatch.pl', 'Piotr Cymcyk',
})


# ---------------------------------------------------------------------------
# Fetch / clean / translate
# ---------------------------------------------------------------------------

def _clean_html(raw):
    if not raw:
        return ""
    text = re.sub(r'<[^<]+?>', '', raw)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&quot;', '"').replace('&#39;', "'").replace('&apos;', "'"))
    return ' '.join(text.split())


def _trim_sentences(text, max_sentences=3):
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    snippet = ' '.join(sentences[:max_sentences])
    if snippet and snippet[-1] not in '.!?':
        snippet += '.'
    return snippet


def _parse_date(entry):
    for key in ('published_parsed', 'updated_parsed'):
        v = entry.get(key)
        if v:
            try:
                return datetime.datetime(*v[:6], tzinfo=datetime.timezone.utc)
            except (TypeError, ValueError):
                continue
    return datetime.datetime.now(datetime.timezone.utc)


def _decode_google_news_url(url):
    if not url or 'news.google.com' not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        result = gnewsdecoder(url, interval=0)
        if result and result.get('status') and result.get('decoded_url'):
            return result['decoded_url']
    except Exception as e:
        logger.warning(f"Google News URL decode failed: {e}")
    return url


def _translate(text, target='pl'):
    if not text:
        return ''
    try:
        return GoogleTranslator(source='auto', target=target).translate(text)
    except Exception as e:
        logger.warning(f"Translation error: {e}")
        return text


FEED_USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def fetch_source(name, url, kind):
    feed = None
    last_err = None
    # A couple of sources (e.g. Benchmark.pl) intermittently reset the
    # connection - feedparser's default user agent looks bot-like enough that
    # some sites' edge protection drops it, and the occasional reset is also
    # just network flakiness. One retry with a browser-like UA covers both.
    for attempt in range(2):
        try:
            feed = feedparser.parse(url, agent=FEED_USER_AGENT)
            if feed.entries or not feed.get('bozo_exception'):
                break
            last_err = feed.get('bozo_exception')
        except Exception as e:
            last_err = e
            feed = None
    if feed is None:
        logger.error(f"Failed to fetch {name} ({url}): {last_err}")
        return []
    try:
        items = []
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            title = _clean_html(entry.get('title', ''))
            if not title:
                continue
            summary_raw = _clean_html(entry.get('summary', entry.get('description', '')))
            link = entry.get('link', '')
            date = _parse_date(entry)
            age_hours = (datetime.datetime.now(datetime.timezone.utc) - date).total_seconds() / 3600
            if age_hours < 0 or age_hours > FRESHNESS_WINDOW_HOURS:
                continue
            items.append({
                'title': title,
                'summary_raw': _trim_sentences(summary_raw, 3),
                'url': link,
                'kind': kind,
                'date': date,
                'source': name,
            })
        return items
    except Exception as e:
        logger.error(f"Failed to fetch {name} ({url}): {e}")
        return []


def _word_set(text):
    return set(re.sub(r'[^\w\s]', ' ', (text or '').lower()).split())


def _title_overlap(a, b):
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0
    return len(wa & wb) / min(len(wa), len(wb))


def _dedupe(items):
    """Same event reported by several outlets collapses into one card - the
    first (most recent, since items are pre-sorted) survives and gets a
    confirmed_by count of how many distinct sources reported it."""
    kept = []
    for item in items:
        match = next((k for k in kept if k['source'] != item['source']
                       and _title_overlap(item['title'], k['title']) > DEDUPE_OVERLAP_THRESHOLD), None)
        if match:
            match['confirmed_by'] = match.get('confirmed_by', 1) + 1
        else:
            item['confirmed_by'] = 1
            kept.append(item)
    return kept


# A flat "take the 50 newest across the whole section" cap after dedup let a
# single high-volume source (e.g. PAP/Reuters/Antyweb routinely return 50-100
# items a day) fill most or all of those slots, leaving low-volume sources
# (Benchmark.pl, Instalki.pl...) with nothing even though they had something
# to say - this was the actual root cause of "I gave you this source but it
# never shows up" (confirmed: Benchmark.pl's feed itself works fine, it was
# just always crowded out downstream). Round-robin by source instead: one
# pass takes each source's next-most-recent item before any source gets a
# second, guaranteeing every configured source a fair shot at a slot. Same
# fix already proven in the medint project's fetch_data.py.
PER_SOURCE_SECTION_CAP = 15


def _cap_fairly_by_source(items, total_cap, per_source_cap):
    by_source = {}
    for item in items:  # already sorted newest-first
        by_source.setdefault(item['source'], []).append(item)
    result = []
    while len(result) < total_cap:
        progressed = False
        for bucket in by_source.values():
            if not bucket:
                continue
            if sum(1 for r in result if r['source'] == bucket[0]['source']) >= per_source_cap:
                continue
            result.append(bucket.pop(0))
            progressed = True
            if len(result) >= total_cap:
                break
        if not progressed:
            break
    result.sort(key=lambda it: it['date'], reverse=True)
    return result


def fetch_section(name_to_url_kind):
    """Fetches every source in a section concurrently, dedupes, caps, then
    translates+decodes only the items that actually survive (cheaper and
    avoids wasting a translation call on something we're about to drop)."""
    with ThreadPoolExecutor(max_workers=max(1, len(name_to_url_kind))) as executor:
        futures = [executor.submit(fetch_source, name, url, kind) for name, url, kind in name_to_url_kind]
        all_items = [item for f in futures for item in f.result()]

    all_items.sort(key=lambda it: it['date'], reverse=True)
    deduped = _cap_fairly_by_source(_dedupe(all_items), MAX_ARTICLES_PER_SECTION, PER_SOURCE_SECTION_CAP)

    def _finalize(item):
        title_original = item['title']
        if item['kind'] == 'google_news':
            item['url'] = _decode_google_news_url(item['url'])
        if item['source'] in POLISH_SOURCES:
            item['title'] = title_original
            item['summary'] = item['summary_raw']
        else:
            item['title'] = _translate(title_original)
            item['summary'] = _translate(item['summary_raw'])
        item['date'] = item['date'].strftime('%Y-%m-%dT%H:%M:%SZ')
        del item['summary_raw']
        del item['kind']
        return item

    finalized = list(TRANSLATE_EXECUTOR.map(_finalize, deduped))
    return finalized


# ---------------------------------------------------------------------------
# Derived intelligence: critical alerts / map / instability / investment
# ---------------------------------------------------------------------------

CRITICAL_KEYWORDS = [
    'wybuch wojny', 'wypowiedzenie wojny', 'zamach', 'eksplozja', 'wybuch bomby',
    'masowe ofiary', 'masakr', 'stan wyjątkowy', 'pucz', 'zamach stanu',
    'broń nuklearna', 'broń chemiczna', 'atak nuklearny', 'katastrofa nuklearna',
    'epidemia', 'pandemia', 'tsunami', 'trzęsienie ziemi', 'erupcja wulkanu',
    'cyberatak na infrastrukturę', 'wyciek danych rządowych',
]

# (region label, lat, lng, [regex-safe keyword stems with word boundaries])
CONFLICT_REGIONS = [
    ("Ukraina", 48.38, 31.18, [r"ukrain\w*"]),
    ("Strefa Gazy / Izrael", 31.5, 34.47, [r"gaz[ae]\b", r"izrael\w*", r"hamas\w*"]),
    ("Liban", 33.89, 35.50, [r"liban\w*", r"hezbollah\w*"]),
    ("Iran", 32.43, 53.69, [r"iran\w*"]),
    ("Syria", 34.80, 38.99, [r"syri\w*"]),
    ("Sudan", 12.86, 30.22, [r"sudan\w*"]),
    ("DR Kongo", -4.32, 21.76, [r"kongij\w*", r"kongo\b"]),
    ("Tajwan", 23.7, 121.0, [r"tajwa\w*"]),
    ("Korea Północna", 40.34, 127.51, [r"korea północna", r"korei północnej", r"pjongjang\w*"]),
    ("Mjanma", 19.75, 96.1, [r"mjanm\w*", r"birm\w*"]),
    ("Jemen", 15.55, 48.52, [r"jemen\w*", r"huti\w*"]),
    ("Afganistan", 33.94, 67.71, [r"afganist\w*", r"talib\w*"]),
    ("Mali / Sahel", 17.57, -4.0, [r"\bmali\b", r"sahel\w*"]),
    ("Somalia", 5.15, 46.20, [r"somali\w*", r"al-shabaab\w*"]),
    ("Wenezuela", 6.42, -66.59, [r"wenezuel\w*"]),
    ("Haiti", 18.97, -72.29, [r"haiti\w*"]),
]

# Broader country lookup for map placement, used by everything except the
# 'conflict' layer (which intentionally sticks to the curated CONFLICT_REGIONS
# above for instability scoring). Without this, a critical/disaster/cyber
# story about a country that isn't one of those 15 war zones - e.g. Kenya,
# Japan, Brazil - silently got no marker at all, even when it was severe
# enough to show up in the critical-alerts ticker.
EXTRA_COUNTRY_COORDS = [
    # Europa
    ("Polska", 52.0, 19.0, [r"polsk\w*"]),
    ("Wielka Brytania", 55.0, -3.0, [r"wielk\w* brytani\w*", r"\buk\b", r"brytyjsk\w*", r"londyn\w*"]),
    ("Francja", 46.6, 2.2, [r"francj\w*", r"francusk\w*"]),
    ("Niemcy", 51.2, 10.4, [r"niemc\w*", r"niemieck\w*"]),
    ("Rosja", 61.5, 105.3, [r"rosj\w*", r"rosyjsk\w*", r"kreml\w*"]),
    ("Włochy", 41.9, 12.6, [r"włoch\w*", r"włosk\w*"]),
    ("Hiszpania", 40.5, -3.7, [r"hiszpani\w*", r"hiszpańsk\w*"]),
    ("Holandia", 52.1, 5.3, [r"holandi\w*", r"niderland\w*"]),
    ("Szwecja", 60.1, 18.6, [r"szwecj\w*", r"szwedzk\w*"]),
    ("Norwegia", 60.5, 8.5, [r"norwegi\w*", r"norwesk\w*"]),
    ("Dania", 56.0, 9.5, [r"duńsk\w*", r"kopenhag\w*"]),
    ("Finlandia", 64.0, 26.0, [r"finlandi\w*", r"fińsk\w*"]),
    ("Irlandia", 53.4, -8.2, [r"irlandi\w*", r"irlandzk\w*"]),
    ("Belgia", 50.8, 4.4, [r"belgi\w*", r"bruksel\w*"]),
    ("Austria", 47.5, 14.5, [r"austri\w*", r"wiede[nń]\w*"]),
    ("Szwajcaria", 46.8, 8.2, [r"szwajcari\w*", r"szwajcarsk\w*"]),
    ("Portugalia", 39.4, -8.2, [r"portugal\w*"]),
    ("Grecja", 39.1, 21.8, [r"grecj\w*", r"greck\w*", r"aten\w*"]),
    ("Czechy", 49.8, 15.5, [r"czech\w*", r"prag\w*"]),
    ("Słowacja", 48.7, 19.7, [r"słowacj\w*", r"słowack\w*"]),
    ("Węgry", 47.2, 19.5, [r"węgr\w*", r"węgiersk\w*", r"budapeszt\w*"]),
    ("Rumunia", 45.9, 24.97, [r"rumuni\w*"]),
    ("Bułgaria", 42.7, 25.5, [r"bułgari\w*"]),
    ("Serbia", 44.0, 21.0, [r"serbi\w*", r"belgrad\w*"]),
    ("Litwa", 55.2, 23.9, [r"litw\w*", r"litewsk\w*", r"wilno\w*"]),
    ("Łotwa", 56.9, 24.6, [r"łotw\w*", r"\bryg[ai]\b"]),
    ("Estonia", 58.6, 25.0, [r"estoni\w*", r"talin\w*"]),
    ("Białoruś", 53.7, 27.9, [r"białorus\w*", r"białorusk\w*", r"miński\w*", r"miska\w*"]),
    # Ameryka Północna i Południowa
    ("Stany Zjednoczone", 39.8, -98.6, [r"\busa\b", r"stan(y|ów)? zjednoczon\w*", r"amerykańsk\w*", r"washington\w*", r"waszyngton\w*"]),
    ("Kanada", 56.1, -106.3, [r"kanad\w*"]),
    ("Meksyk", 23.6, -102.5, [r"meksyk\w*"]),
    ("Brazylia", -14.2, -51.9, [r"brazyli\w*"]),
    ("Argentyna", -38.4, -63.6, [r"argentyn\w*"]),
    ("Chile", -35.7, -71.5, [r"\bchile\b", r"chilijsk\w*"]),
    ("Kolumbia", 4.6, -74.3, [r"kolumbi\w*"]),
    ("Peru", -9.2, -75.0, [r"\bperu\b", r"peruwia[nń]\w*"]),
    # "Kuba" is also a common Polish first name (diminutive of Jakub) - too
    # risky as a bare stem, so only the unambiguous capital name is used.
    ("Kuba", 21.5, -77.8, [r"\bhawan\w*"]),
    ("Ekwador", -1.83, -78.18, [r"ekwador\w*"]),
    ("Boliwia", -16.3, -63.6, [r"boliwi\w*"]),
    # Azja
    ("Chiny", 35.0, 103.8, [r"\bchin\w*", r"\bpekin\w*"]),
    ("Indie", 21.0, 78.0, [r"indi\w*", r"hindus\w*"]),
    ("Japonia", 36.2, 138.2, [r"japoni\w*", r"japońsk\w*"]),
    ("Korea Południowa", 35.9, 127.7, [r"korea południow\w*", r"korei południow\w*", r"seul\w*"]),
    ("Pakistan", 30.4, 69.3, [r"pakista[nń]\w*"]),
    ("Bangladesz", 23.7, 90.4, [r"bangladesz\w*"]),
    ("Indonezja", -0.8, 113.9, [r"indonezj\w*"]),
    ("Filipiny", 12.9, 121.8, [r"filipin\w*", r"manil\w*"]),
    ("Wietnam", 14.1, 108.3, [r"wietnam\w*"]),
    ("Tajlandia", 15.9, 100.99, [r"tajlandi\w*"]),
    ("Malezja", 4.2, 101.9, [r"malezj\w*"]),
    ("Singapur", 1.35, 103.8, [r"singapur\w*"]),
    ("Arabia Saudyjska", 23.9, 45.1, [r"arabi\w* saudyjsk\w*", r"rijad\w*"]),
    ("Zjednoczone Emiraty Arabskie", 23.4, 53.8, [r"emirat\w* arabski\w*", r"dubaj\w*", r"abu zabi\w*"]),
    # "katar" is also the everyday Polish word for "a cold" - too risky as a
    # bare stem, so only the unambiguous capital name is used.
    ("Katar", 25.3, 51.2, [r"\bdoha\w*"]),
    ("Irak", 33.2, 43.7, [r"irak\w*", r"bagdad\w*"]),
    ("Jordania", 31.2, 36.9, [r"jordani\w*", r"amman\w*"]),
    # Afryka
    ("Egipt", 26.8, 30.8, [r"egipt\w*"]),
    ("Nigeria", 9.08, 8.68, [r"nigeri\w*"]),
    ("Kenia", -0.0236, 37.9, [r"keni\w*"]),
    ("Etiopia", 9.1, 40.5, [r"etiopi\w*"]),
    ("RPA", -30.6, 22.9, [r"\brpa\b", r"południow\w* afryk\w*"]),
    ("Algieria", 28.0, 1.66, [r"algieri\w*", r"algiersk\w*"]),
    ("Maroko", 31.8, -7.1, [r"marok[ao]\w*"]),
    ("Tunezja", 33.9, 9.5, [r"tunezj\w*"]),
    ("Libia", 26.3, 17.2, [r"libi\w*"]),
    ("Ghana", 7.9, -1.0, [r"ghan\w*"]),
    ("Tanzania", -6.4, 34.9, [r"tanzani\w*"]),
    ("Uganda", 1.37, 32.3, [r"ugand\w*"]),
    ("Zimbabwe", -19.0, 29.15, [r"zimbabwe\w*"]),
    # Oceania
    ("Australia", -25.3, 133.8, [r"australi\w*"]),
    ("Nowa Zelandia", -40.9, 174.9, [r"now\w* zelandi\w*"]),
    ("Papua-Nowa Gwinea", -6.3, 143.96, [r"papu[ai][ -]now\w* gwine\w*"]),
    ("Fidżi", -17.7, 178.07, [r"fidż\w*"]),
    ("Vanuatu", -15.38, 166.96, [r"vanuatu\w*"]),
    ("Wyspy Salomona", -9.6, 160.16, [r"wysp\w* salomon\w*"]),
    ("Samoa", -13.76, -172.1, [r"\bsamo[ai]\b", r"samoańsk\w*"]),
    ("Tonga", -21.18, -175.2, [r"\btong\w* (?=wysp|ocean|pacyf|król)"]),
    ("Kiribati", 1.87, -157.36, [r"kiribati\w*"]),
    ("Mikronezja", 6.92, 158.25, [r"mikronezj\w*"]),
    ("Palau", 7.51, 134.58, [r"\bpalau\b"]),
    ("Wyspy Marshalla", 7.13, 171.18, [r"wysp\w* marshall\w*"]),
    ("Nauru", -0.52, 166.93, [r"nauru\w*"]),
    ("Tuvalu", -7.11, 177.65, [r"tuvalu\w*"]),
    ("Turcja", 38.9, 35.2, [r"turcj\w*", r"tureck\w*"]),
    # Europa (uzupełnienie)
    ("Islandia", 64.96, -19.02, [r"islandi\w*", r"reykjavik\w*"]),
    ("Luksemburg", 49.82, 6.13, [r"luksemburg\w*"]),
    ("Malta", 35.94, 14.38, [r"\bmalt[ae]\b", r"malta[nń]sk\w*"]),
    ("Cypr", 35.13, 33.43, [r"cypr\w*"]),
    ("Mołdawia", 47.41, 28.37, [r"mołdawi\w*", r"mołdawsk\w*"]),
    ("Albania", 41.15, 20.17, [r"albani\w*"]),
    ("Bośnia i Hercegowina", 43.92, 17.68, [r"bo[sś]ni\w*"]),
    ("Czarnogóra", 42.71, 19.37, [r"czarnogór\w*"]),
    ("Macedonia Północna", 41.61, 21.75, [r"macedoni\w*"]),
    ("Kosowo", 42.6, 20.9, [r"kosow\w* (?=serbi|albań|region|niepodleg|konflikt)"]),
    ("Monako", 43.74, 7.43, [r"monak\w*"]),
    ("Andora", 42.55, 1.6, [r"andor\w*"]),
    ("Liechtenstein", 47.17, 9.55, [r"liechtenstein\w*"]),
    ("San Marino", 43.94, 12.46, [r"san marino\w*"]),
    ("Watykan", 41.9, 12.45, [r"watykan\w*"]),
    # Azja (uzupełnienie)
    ("Sri Lanka", 7.87, 80.77, [r"sri lank\w*"]),
    ("Nepal", 28.39, 84.12, [r"nepal\w*"]),
    ("Bhutan", 27.51, 90.43, [r"bhutan\w*"]),
    ("Kambodża", 12.57, 104.99, [r"kambodż\w*"]),
    ("Laos", 19.86, 102.5, [r"\blaos\w*"]),
    ("Brunei", 4.54, 114.73, [r"brunei\w*"]),
    ("Mongolia", 46.86, 103.85, [r"mongoli\w*", r"ułan ?bator\w*"]),
    ("Kazachstan", 48.02, 66.92, [r"kazachstan\w*"]),
    ("Uzbekistan", 41.38, 64.59, [r"uzbekistan\w*"]),
    ("Turkmenistan", 38.97, 59.56, [r"turkmenistan\w*"]),
    ("Tadżykistan", 38.86, 71.28, [r"tadżykistan\w*"]),
    ("Kirgistan", 41.2, 74.77, [r"kirgist\w*"]),
    ("Azerbejdżan", 40.14, 47.58, [r"azerbejdżan\w*"]),
    ("Armenia", 40.07, 45.04, [r"armeni\w*", r"erewa[nń]\w*"]),
    ("Gruzja", 42.32, 43.36, [r"gruzj\w*", r"gruzi[nń]sk\w*", r"tbilisi\w*"]),
    ("Izrael", 31.0, 34.8, [r"izrael\w*"]),
    ("Kuwejt", 29.31, 47.48, [r"kuwejt\w*"]),
    ("Bahrajn", 26.07, 50.56, [r"bahrajn\w*"]),
    ("Oman", 21.51, 55.92, [r"\boman\w*"]),
    ("Timor Wschodni", -8.87, 125.73, [r"timor\w*"]),
    # Afryka (uzupełnienie)
    ("Senegal", 14.5, -14.45, [r"senegal\w*"]),
    ("Mauretania", 20.25, -10.35, [r"mauretani\w*"]),
    ("Burkina Faso", 12.24, -1.56, [r"burkina\w*"]),
    # "Niger" needs an exact word boundary on both sides - without it,
    # \w* after the stem would also swallow "Nigeria".
    ("Niger", 17.6, 8.08, [r"\bniger\b", r"\bnigerski\w*"]),
    # "czad" is also common Polish slang for "awesome/cool" - too risky as a
    # bare stem, so only the unambiguous capital name is used.
    ("Czad", 15.45, 18.73, [r"ndżamen\w*"]),
    ("Kamerun", 7.37, 12.35, [r"kamerun\w*"]),
    ("Republika Środkowoafrykańska", 6.61, 20.94, [r"środkowoafrykańsk\w*"]),
    ("Gabon", 0.8, 11.6, [r"gabon\w*"]),
    ("Angola", -11.2, 17.87, [r"angol[ai]\w*"]),
    ("Zambia", -13.13, 27.85, [r"zambi\w*"]),
    ("Mozambik", -18.67, 35.53, [r"mozambik\w*"]),
    ("Malawi", -13.25, 34.3, [r"malawi\w*"]),
    ("Botswana", -22.33, 24.68, [r"botswan\w*"]),
    ("Namibia", -22.96, 18.49, [r"namibi\w*"]),
    ("Lesotho", -29.61, 28.23, [r"lesotho\w*"]),
    ("Eswatini", -26.52, 31.47, [r"eswatini\w*", r"suazi\w*"]),
    ("Madagaskar", -18.77, 46.87, [r"madagaskar\w*"]),
    ("Mauritius", -20.35, 57.55, [r"mauritius\w*"]),
    ("Seszele", -4.68, 55.49, [r"seszel\w*"]),
    ("Komory", -11.88, 43.87, [r"komor\w* (?=wysp|ocean|archipelag)"]),
    ("Dżibuti", 11.83, 42.59, [r"dżibuti\w*"]),
    ("Erytrea", 15.18, 39.78, [r"erytre\w*"]),
    ("Rwanda", -1.94, 29.87, [r"rwand\w*"]),
    ("Burundi", -3.37, 29.92, [r"burundi\w*"]),
    ("Wybrzeże Kości Słoniowej", 7.54, -5.55, [r"wybrzeż\w* kości słoniow\w*"]),
    # Multi-word "Gwinea ..." variants are checked before the bare "Gwinea"
    # stem (further below) so a mention of Bissau/Równikowa isn't swallowed
    # by the shorter, more generic pattern first.
    ("Gwinea Bissau", 11.8, -15.18, [r"gwine\w* bissau\w*"]),
    ("Gwinea Równikowa", 1.65, 10.27, [r"gwine\w* równikow\w*"]),
    ("Gwinea", 9.95, -9.7, [r"\bgwine[ai]\b", r"gwinejsk\w*"]),
    ("Sierra Leone", 8.46, -11.78, [r"sierra leone\w*"]),
    ("Liberia", 6.43, -9.43, [r"liberi\w*"]),
    ("Togo", 8.62, 0.82, [r"\btogo\b"]),
    ("Benin", 9.31, 2.32, [r"\bbenin\w*"]),
    ("Republika Zielonego Przylądka", 16.0, -24.01, [r"zielon\w* przyl[ąa]dk\w*"]),
    ("Gambia", 13.44, -15.31, [r"gambi\w*"]),
    ("Sudan Południowy", 6.88, 31.31, [r"sudan\w* południow\w*", r"\bjuba\b"]),
    # Ameryka Środkowa i Karaiby (uzupełnienie)
    ("Gwatemala", 15.78, -90.23, [r"gwatemal\w*"]),
    ("Honduras", 15.2, -86.24, [r"hondurański\w*", r"\bhonduras\w*"]),
    ("Salwador", 13.79, -88.9, [r"salwador\w*"]),
    ("Nikaragua", 12.87, -85.21, [r"nikaragu\w*"]),
    ("Kostaryka", 9.75, -83.75, [r"kostaryk\w*", r"kostarykański\w*"]),
    ("Panama", 8.54, -80.78, [r"panamsk\w*", r"\bpanam[ąy]\b", r"panama (?=kanał|miast)"]),
    ("Jamajka", 18.11, -77.3, [r"jamajk\w*", r"jamajski\w*"]),
    ("Dominikana", 18.74, -70.16, [r"dominikan\w*"]),
    ("Bahamy", 25.03, -77.4, [r"bahamy\w*", r"bahamski\w*"]),
    ("Trynidad i Tobago", 10.69, -61.22, [r"trynidad\w*"]),
    ("Paragwaj", -23.44, -58.44, [r"paragwaj\w*"]),
    ("Urugwaj", -32.52, -55.77, [r"urugwaj\w*"]),
]

# Used for cyber/disaster/gps-jamming/critical placement: conflict regions
# plus the broader country list above, so matching isn't limited to the 15
# active war zones.
ALL_REGION_COORDS = CONFLICT_REGIONS + EXTRA_COUNTRY_COORDS

NATURAL_DISASTER_KEYWORDS = [
    r"powod[zź]\w*", r"huragan\w*", r"trz[ęe]sieni\w* ziemi", r"po[zż]ar\w* las\w*",
    r"tornado\w*", r"erupcj\w* wulkan\w*", r"susz\w*", r"tajfun\w*",
]

GPS_JAMMING_KEYWORDS = [r"zagłuszani\w* gps", r"zakłócen\w* gps", r"gps jamming"]

WEATHER_ALERT_KEYWORDS = [r"alert pogodow\w*", r"ostrzeżeni\w* pogodow\w*", r"fala upał\w*", r"mróz\w* dotkliw\w*"]

INSTABILITY_KEYWORD_WEIGHTS = [
    (r"wojn\w*", 3), (r"atak\w*", 2), (r"zamach\w*", 3), (r"zamieszki\w*", 2),
    (r"przewrót\w*", 3), (r"kryzys\w*", 1), (r"katastrof\w*", 2), (r"powod[zź]\w*", 2),
    (r"susz\w*", 1), (r"terroryzm\w*", 2), (r"napad\w*", 1), (r"rozbój\w*", 1),
    # Natural disasters that threaten human life count toward instability too
    # (user-requested: a Venezuela earthquake wasn't reflected here at all,
    # since none of the keywords above reliably match an earthquake report -
    # "katastrofa" alone is too generic a word for an article to actually use).
    (r"trz[ęe]sieni\w* ziemi", 3), (r"tsunami\w*", 3), (r"huragan\w*", 2),
    (r"tajfun\w*", 2), (r"erupcj\w* wulkan\w*", 3), (r"po[zż]ar\w* las\w*", 2),
]

INVESTMENT_RULES = [
    (r"wojn\w*|konflikt zbrojn\w*|napięci\w* zbrojn\w*",
     "Sektor obronny", "np. ETF iShares Global Aerospace & Defense (ITA)"),
    (r"sankcj\w* energetyczn\w*|ropa\w*|gaz ziemny|OPEC",
     "Sektor energetyczny/surowcowy", "np. ETF Energy Select Sector (XLE)"),
    (r"inflacj\w*|stop\w* procentow\w*|recesj\w*",
     "Złoto i obligacje skarbowe", "np. ETF na złoto (GLD) lub obligacje skarbowe"),
    (r"cyberatak\w*|wyciek danych|ransomware",
     "Sektor cyberbezpieczeństwa", "np. ETF Global X Cybersecurity (BUG)"),
    (r"susz\w*|kryzys żywnościow\w*|ceny żywności",
     "Sektor agro", "np. ETF Invesco DB Agriculture (DBA)"),
]


def _matches_any(text_lower, patterns):
    return any(re.search(p, text_lower) for p in patterns)


def _all_text(sections, *keys):
    out = []
    for key in keys:
        for item in sections.get(key, []):
            out.append((item, f"{item['title']} {item['summary']}".lower()))
    return out


def build_critical_alerts(sections):
    alerts = []
    for item, text in _all_text(sections, 'poland', 'world_security', 'world_politics'):
        if any(kw in text for kw in CRITICAL_KEYWORDS):
            alerts.append(item['title'])
    return alerts[:5] or ["Sytuacja stabilna - brak nowych alarmów krytycznych"]


def build_map_features(sections):
    features = []
    pool = _all_text(sections, 'poland', 'world_security', 'world_politics', 'cybersecurity')
    for region, lat, lng, patterns in CONFLICT_REGIONS:
        hit = next((item for item, text in pool if _matches_any(text, patterns)), None)
        if hit:
            features.append({
                'lat': lat, 'lng': lng, 'type': 'conflict', 'region': region,
                'description': _trim_sentences(hit['summary'], 1) or hit['title'],
                'url': hit['url'],
            })
    for item, text in _all_text(sections, 'cybersecurity'):
        for region, lat, lng, patterns in ALL_REGION_COORDS:
            if _matches_any(text, patterns):
                features.append({
                    'lat': lat + 0.5, 'lng': lng + 0.5, 'type': 'cyber', 'region': region,
                    'description': _trim_sentences(item['summary'], 1) or item['title'],
                    'url': item['url'],
                })
                break
    for item, text in pool:
        if _matches_any(text, NATURAL_DISASTER_KEYWORDS):
            for region, lat, lng, patterns in ALL_REGION_COORDS:
                if _matches_any(text, patterns):
                    features.append({
                        'lat': lat - 0.5, 'lng': lng - 0.5, 'type': 'disaster', 'region': region,
                        'description': _trim_sentences(item['summary'], 1) or item['title'],
                        'url': item['url'],
                    })
                    break
        if _matches_any(text, GPS_JAMMING_KEYWORDS):
            for region, lat, lng, patterns in ALL_REGION_COORDS:
                if _matches_any(text, patterns):
                    features.append({
                        'lat': lat + 0.3, 'lng': lng - 0.3, 'type': 'gps_jamming', 'region': region,
                        'description': _trim_sentences(item['summary'], 1) or item['title'],
                        'url': item['url'],
                    })
                    break

    # Critical alerts must always end up on the map if a country can be
    # identified, regardless of whether that country is an active conflict
    # zone - this is what the ticker's "ALARM" banner refers to, so the map
    # should be able to point at it too.
    critical_pool = _all_text(sections, 'poland', 'world_security', 'world_politics', 'technology', 'cybersecurity', 'finance')
    seen_critical_regions = set()
    for item, text in critical_pool:
        if not any(kw in text for kw in CRITICAL_KEYWORDS):
            continue
        for region, lat, lng, patterns in ALL_REGION_COORDS:
            if region in seen_critical_regions:
                continue
            if _matches_any(text, patterns):
                seen_critical_regions.add(region)
                features.append({
                    'lat': lat - 0.3, 'lng': lng + 0.3, 'type': 'critical', 'region': region,
                    'description': _trim_sentences(item['summary'], 1) or item['title'],
                    'url': item['url'],
                })
                break
    return features


MAP_HISTORY_PATH = 'map_history.json'
MAP_FEATURE_TTL_HOURS = 24


def merge_map_features(fresh_features):
    """Each run only re-derives markers from whatever's currently in the
    top of each section, so a region could otherwise blink on and off the
    map every few minutes purely because its source article rotated out of
    a section's top-N. Persisting last-seen times in MAP_HISTORY_PATH (also
    committed to the repo, like data.json) keeps a marker visible for a full
    MAP_FEATURE_TTL_HOURS since it was last (re)detected, then drops it -
    giving a stable situational picture instead of a flickering one, with
    no behaviour change to how each marker's description/url/source works."""
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(MAP_HISTORY_PATH, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}

    for feat in fresh_features:
        key = f"{feat['type']}|{feat['region']}"
        history[key] = {**feat, 'last_seen': now.strftime('%Y-%m-%dT%H:%M:%SZ')}

    alive = {}
    for key, entry in history.items():
        last_seen_raw = entry.get('last_seen')
        try:
            last_seen = datetime.datetime.strptime(last_seen_raw, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError):
            continue
        if (now - last_seen).total_seconds() <= MAP_FEATURE_TTL_HOURS * 3600:
            alive[key] = entry

    with open(MAP_HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(alive, f, ensure_ascii=False, indent=2)

    return [
        {'lat': e['lat'], 'lng': e['lng'], 'type': e['type'], 'region': e['region'],
         'description': e['description'], 'url': e['url']}
        for e in alive.values()
    ]


def build_instability(sections):
    """Returns each region's score plus the actual articles that drove it
    ("reasons") - the dashboard's instability tile lets a user click a
    country to see exactly what happened, rather than just a bare percentage
    with no explanation of why it's classified as unstable."""
    pool = _all_text(sections, 'poland', 'world_security', 'world_politics')
    scores = {}
    reasons = {}
    for region, _, _, patterns in CONFLICT_REGIONS:
        score = 0
        matched = []
        for item, text in pool:
            if not _matches_any(text, patterns):
                continue
            item_weight = sum(weight for kw, weight in INSTABILITY_KEYWORD_WEIGHTS if re.search(kw, text))
            if item_weight:
                score += item_weight
                matched.append((item_weight, item))
        if score:
            scores[region] = score
            matched.sort(key=lambda pair: pair[0], reverse=True)
            reasons[region] = [
                {'title': it['title'], 'url': it['url'], 'source': it.get('source', '')}
                for _, it in matched[:3]
            ]
    if not scores:
        return []
    max_score = max(scores.values())
    # No top-N slice - CONFLICT_REGIONS has only 15 entries total, so there's
    # nothing to artificially cap here; every region that actually scored
    # should be visible (the tile's own scrollbar handles a long list).
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {'name': name, 'score': round(100 * s / max_score), 'reasons': reasons.get(name, [])}
        for name, s in ranked
    ]


def build_investment_picks(sections):
    pool = _all_text(sections, 'poland', 'world_security', 'world_politics', 'finance')
    picks = []
    seen_sectors = set()
    for pattern, sector, instrument in INVESTMENT_RULES:
        if sector in seen_sectors:
            continue
        if any(re.search(pattern, text) for _, text in pool):
            picks.append({'sector': sector, 'instrument': instrument})
            seen_sectors.add(sector)
    return picks[:3]


def _fetch_yahoo_chart_series(symbol):
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=6mo&interval=1d",
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()['chart']['result'][0]
    timestamps = result['timestamp']
    closes = result['indicators']['quote'][0]['close']
    series = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
        series[day] = close
    return series


def fetch_sp500_trend():
    try:
        sp500_usd = _fetch_yahoo_chart_series('%5EGSPC')
        try:
            usdpln = _fetch_yahoo_chart_series('USDPLN=X')
        except Exception as e:
            logger.error(f"Failed to fetch USDPLN rate: {e}")
            usdpln = {}

        fallback_rate = list(usdpln.values())[-1] if usdpln else 4.0
        dates = sorted(sp500_usd.keys())
        prices_pln = []
        for day in dates:
            rate = usdpln.get(day, fallback_rate)
            prices_pln.append(round(sp500_usd[day] * rate, 2))
        return {'dates': dates, 'prices': prices_pln, 'currency': 'PLN'}
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 trend: {e}")
        return {'dates': [], 'prices': [], 'currency': 'PLN'}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # All sections (and the S&P 500 trend) are independent I/O-bound fetches,
    # so they run concurrently rather than one-after-another - wall-clock
    # time becomes the slowest single section instead of the sum of all of them.
    with ThreadPoolExecutor(max_workers=len(sources.ALL_SECTIONS) + 1) as executor:
        logger.info(f"Fetching {len(sources.ALL_SECTIONS)} sections concurrently...")
        section_futures = {
            key: executor.submit(fetch_section, source_list)
            for key, source_list in sources.ALL_SECTIONS.items()
        }
        sp500_future = executor.submit(fetch_sp500_trend)
        sections = {key: f.result() for key, f in section_futures.items()}
        sp500_trend = sp500_future.result()

    output = {
        'last_updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'poland': sections['poland'],
        'world_security': sections['world_security'],
        'world_politics': sections['world_politics'],
        'technology': sections['technology'],
        'cybersecurity': sections['cybersecurity'],
        'finance': sections['finance'],
        'critical_alerts': build_critical_alerts(sections),
        'map_features': merge_map_features(build_map_features(sections)),
        'instability': build_instability(sections),
        'investment_picks': build_investment_picks(sections),
        'sp500_trend': sp500_trend,
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("Done - data.json updated.")


if __name__ == '__main__':
    main()
