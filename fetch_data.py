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

MAX_ARTICLES_PER_SOURCE = 8
MAX_ARTICLES_PER_SECTION = 24
FRESHNESS_WINDOW_HOURS = 72
DEDUPE_OVERLAP_THRESHOLD = 0.5


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


def fetch_source(name, url, kind):
    try:
        feed = feedparser.parse(url)
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


def fetch_section(name_to_url_kind):
    """Fetches every source in a section concurrently, dedupes, caps, then
    translates+decodes only the items that actually survive (cheaper and
    avoids wasting a translation call on something we're about to drop)."""
    with ThreadPoolExecutor(max_workers=max(1, len(name_to_url_kind))) as executor:
        futures = [executor.submit(fetch_source, name, url, kind) for name, url, kind in name_to_url_kind]
        all_items = [item for f in futures for item in f.result()]

    all_items.sort(key=lambda it: it['date'], reverse=True)
    deduped = _dedupe(all_items)[:MAX_ARTICLES_PER_SECTION]

    def _finalize(item):
        title_original = item['title']
        if item['kind'] == 'google_news':
            item['url'] = _decode_google_news_url(item['url'])
        item['title'] = _translate(title_original)
        item['summary'] = _translate(item['summary_raw'])
        item['date'] = item['date'].strftime('%Y-%m-%dT%H:%M:%SZ')
        del item['summary_raw']
        del item['kind']
        return item

    with ThreadPoolExecutor(max_workers=min(10, len(deduped) or 1)) as executor:
        finalized = list(executor.map(_finalize, deduped))
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

NATURAL_DISASTER_KEYWORDS = [
    r"powod[zź]\w*", r"huragan\w*", r"trz[ęe]sieni\w* ziemi", r"po[zż]ar\w* las\w*",
    r"tornado\w*", r"erupcj\w* wulkan\w*", r"susz\w*", r"tajfun\w*",
]

GPS_JAMMING_KEYWORDS = [r"zagłuszani\w* gps", r"zakłócen\w* gps", r"gps jamming"]

WEATHER_ALERT_KEYWORDS = [r"alert pogodow\w*", r"ostrzeżeni\w* pogodow\w*", r"fala upał\w*", r"mróz\w* dotkliw\w*"]

INSTABILITY_KEYWORD_WEIGHTS = [
    (r"wojn\w*", 3), (r"atak\w*", 2), (r"zamach\w*", 3), (r"zamieszki\w*", 2),
    (r"przewrót\w*", 3), (r"kryzys\w*", 1), (r"katastrof\w*", 2), (r"powod[zź]\w*", 1),
    (r"susz\w*", 1), (r"terroryzm\w*", 2), (r"napad\w*", 1), (r"rozbój\w*", 1),
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
        for region, lat, lng, patterns in CONFLICT_REGIONS:
            if _matches_any(text, patterns):
                features.append({
                    'lat': lat + 0.5, 'lng': lng + 0.5, 'type': 'cyber', 'region': region,
                    'description': _trim_sentences(item['summary'], 1) or item['title'],
                    'url': item['url'],
                })
                break
    for item, text in pool:
        if _matches_any(text, NATURAL_DISASTER_KEYWORDS):
            for region, lat, lng, patterns in CONFLICT_REGIONS:
                if _matches_any(text, patterns):
                    features.append({
                        'lat': lat - 0.5, 'lng': lng - 0.5, 'type': 'disaster', 'region': region,
                        'description': _trim_sentences(item['summary'], 1) or item['title'],
                        'url': item['url'],
                    })
                    break
        if _matches_any(text, GPS_JAMMING_KEYWORDS):
            for region, lat, lng, patterns in CONFLICT_REGIONS:
                if _matches_any(text, patterns):
                    features.append({
                        'lat': lat + 0.3, 'lng': lng - 0.3, 'type': 'gps_jamming', 'region': region,
                        'description': _trim_sentences(item['summary'], 1) or item['title'],
                        'url': item['url'],
                    })
                    break
    return features


def build_instability(sections):
    pool = _all_text(sections, 'poland', 'world_security', 'world_politics')
    scores = {}
    for region, _, _, patterns in CONFLICT_REGIONS:
        score = 0
        for item, text in pool:
            if not _matches_any(text, patterns):
                continue
            for kw, weight in INSTABILITY_KEYWORD_WEIGHTS:
                if re.search(kw, text):
                    score += weight
        if score:
            scores[region] = score
    if not scores:
        return []
    max_score = max(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return [{'name': name, 'score': round(100 * s / max_score)} for name, s in ranked]


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


def fetch_sp500_trend():
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=6mo&interval=1d",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()['chart']['result'][0]
        timestamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
        dates, prices = [], []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dates.append(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d'))
            prices.append(round(close, 2))
        return {'dates': dates, 'prices': prices}
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 trend: {e}")
        return {'dates': [], 'prices': []}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sections = {}
    for key, source_list in sources.ALL_SECTIONS.items():
        logger.info(f"Fetching section: {key}")
        sections[key] = fetch_section(source_list)

    output = {
        'last_updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'poland': sections['poland'],
        'world_security': sections['world_security'],
        'world_politics': sections['world_politics'],
        'technology': sections['technology'],
        'cybersecurity': sections['cybersecurity'],
        'finance': sections['finance'],
        'critical_alerts': build_critical_alerts(sections),
        'map_features': build_map_features(sections),
        'instability': build_instability(sections),
        'investment_picks': build_investment_picks(sections),
        'sp500_trend': fetch_sp500_trend(),
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("Done - data.json updated.")


if __name__ == '__main__':
    main()
