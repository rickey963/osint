"""
Central registry of approved OSINT dashboard sources, grouped by section.

Every source listed here was explicitly requested by the user - no others
are used. Each entry is (display_name, feed_url, kind):
  - kind == 'rss': the publisher's own native RSS feed (verified working
    directly with a plain GET - confirmed during implementation).
  - kind == 'google_news': the publisher dropped/never offered a public RSS
    feed (Reuters, AP - both discontinued public syndication years ago; PAP
    sits behind an Incapsula bot wall; polskieradio24.pl's RSS path no
    longer serves XML; several finance sites have no discoverable feed at
    all). The *source* is still exactly what the user asked for - only the
    delivery mechanism differs, using the same site:-scoped Google News
    query technique already proven in the medint project.
"""

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search?q={query}&hl=pl&gl=PL&ceid=PL:pl"


def _google_news_url(domain, when="1d"):
    return GOOGLE_NEWS_BASE.format(query=f"site:{domain}+when:{when}")


# --- 1. Polska -------------------------------------------------------------
SOURCES_POLSKA = [
    ("Polsat News", "https://www.polsatnews.pl/rss/wszystkie.xml", "rss"),
    ("Rzeczpospolita", "https://www.rp.pl/rss/4188", "rss"),
    ("Gazeta Wyborcza", "https://wiadomosci.gazeta.pl/pub/rss/wiadomosci.xml", "rss"),
    ("Business Insider Polska", "https://www.businessinsider.com.pl/.feed", "rss"),
    ("PAP", _google_news_url("pap.pl"), "google_news"),
    ("Polskie Radio 24", _google_news_url("polskieradio24.pl"), "google_news"),
]

# --- 2. Świat: Bezpieczeństwo / Polityka ------------------------------------
# Split the same way medint splits sources by tier - kept as two separate
# lists so the dashboard can subdivide "Świat" into the two subcategories
# the user asked for.
SOURCES_SWIAT_BEZPIECZENSTWO = [
    ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml", "rss"),
    ("The Guardian", "https://www.theguardian.com/world/rss", "rss"),
    ("Reuters", _google_news_url("reuters.com"), "google_news"),
]

SOURCES_SWIAT_POLITYKA = [
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "rss"),
    ("Associated Press", _google_news_url("apnews.com"), "google_news"),
]

# --- 3. Technologie ----------------------------------------------------------
SOURCES_TECHNOLOGIE = [
    ("Spider's Web", "https://www.spidersweb.pl/feed", "rss"),
    ("Instalki.pl", "https://www.instalki.pl/feed", "rss"),
    ("Antyweb", "https://antyweb.pl/feed", "rss"),
    ("Benchmark.pl", "https://www.benchmark.pl/rss/aktualnosci-rss.xml", "rss"),
    ("Techmeme", "https://www.techmeme.com/feed.xml", "rss"),
    # hnrss.org is a long-standing, widely-used third-party RSS gateway for
    # Hacker News, which has never offered an official feed of its own.
    ("Hacker News", "https://hnrss.org/frontpage", "rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "rss"),
]

# --- 4. Cyberbezpieczeństwo --------------------------------------------------
SOURCES_CYBER = [
    ("Zaufana Trzecia Strona", "https://zaufanatrzeciastrona.pl/feed/", "rss"),
    ("Niebezpiecznik", "https://niebezpiecznik.pl/feed/", "rss"),
    ("Sekurak", "https://sekurak.pl/feed/", "rss"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews", "rss"),
    ("CyberDefence24", "https://cyberdefence24.pl/_rss", "rss"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/", "rss"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/", "rss"),
    ("The Register", "https://api.theregister.com/api/v1/article?orderBy=published&site_id=2&remapper=rss", "rss"),
]

# --- 6. Finanse --------------------------------------------------------------
SOURCES_FINANSE = [
    ("Money.pl", "https://www.money.pl/rss/rss.xml", "rss"),
    ("Bankier.pl", "https://www.bankier.pl/rss/wiadomosci.xml", "rss"),
    ("Forbes Polska", "https://www.forbes.pl/rss", "rss"),
    ("300Gospodarka", "https://300gospodarka.pl/feed", "rss"),
    ("Inwestomat (Albert Rokicki)", "https://www.inwestomat.eu/feed/", "rss"),
    ("Strefa Inwestorów", "https://strefainwestorow.pl/rss.xml", "rss"),
    ("Forsal.pl", _google_news_url("forsal.pl"), "google_news"),
    ("Biznesradar", _google_news_url("biznesradar.pl"), "google_news"),
    ("StockWatch.pl", _google_news_url("stockwatch.pl"), "google_news"),
    ("Piotr Cymcyk", _google_news_url("cymcyk.pl"), "google_news"),
]

ALL_SECTIONS = {
    "poland": SOURCES_POLSKA,
    "world_security": SOURCES_SWIAT_BEZPIECZENSTWO,
    "world_politics": SOURCES_SWIAT_POLITYKA,
    "technology": SOURCES_TECHNOLOGIE,
    "cybersecurity": SOURCES_CYBER,
    "finance": SOURCES_FINANSE,
}
