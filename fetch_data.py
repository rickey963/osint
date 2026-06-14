import feedparser
import json
import datetime
import os
import re
import random

# CONFIGURATION - All sources must be listed here to ensure they appear on the dashboard
FEEDS = {
    "poland": [
        "https://www.polsatnews.pl/feed",
        "https://www.pap.pl/rss",
        "https://www.polskieradio24.pl/feed",
        "https://wiadomosci.gazeta.pl/rss",
        "https://businessinsider.com.pl/rss"
    ],
    "world_security": [
        "https://feeds.reuters.com/reuters/world",
        "https://www.theguardian.com/world/rss"
    ],
    "world_politics": [
        "https://rss.nytimes.com/services/xml/rss/nytworld",
        "https://apnews.com/hub/ap-top-news.rss"
    ],
    "technology": [
        "https://www.spidersweb.pl/feed",
        "https://techmeme.com/rss",
        "https://antyweb.pl/feed",
        "https://benchmark.pl/feed"
    ],
    "cybersecurity": [
        "https://thehackernews.com/feeds/posts/default",
        "https://www.bleepingcomputer.com/feed/",
        "https://www.theregister.com/headlines.atom"
    ],
    "finance": [
        "https://www.money.pl/rss",
        "https://www.bankier.pl/rss"
    ]
}

def clean_html(raw_html):
    """Remove HTML tags and return clean text."""
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    # Fix indentation/spacing issues in the text
    cleantext = cleantext.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&quot;', '"').replace('&apos;', "'")
    return " ".join(cleantext.split())

def fetch_feed(url):
    """Fetch and parse RSS feed."""
    try:
        url = url.strip()
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:15]:
            title = clean_html(entry.title)
            summary = entry.get('summary', '')
            clean_summary = clean_html(summary)
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', clean_summary) if s.strip()]
            snippet = ". ".join(sentences[:3])
            if snippet and not snippet.endswith('.'):
                snippet += "."

            pub_date_str = entry.get('published', entry.get('updated', datetime.datetime.now().isoformat()))
            articles.append({
                "title": title,
                "snippet": snippet,
                "url": entry.link,
                "date": pub_date_str
            })
        return articles
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []

def main():
    output_data = {
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "poland": [],
        "world_security": [],
        "world_politics": [],
        "technology": [],
        "cybersecurity": [],
        "finance": [],
        "critical_alerts": ["Brak nowych alarmów"],
        "instability": [{"name": "Ukraina", "score": 85}, {"name": "Tajwan", "score": 60}],
        "map_features": [
            {"lat": 48.85, "lng": 2.35, "type": "war", "description": "Przykład: Konflikt w regionie"},
            {"lat": 52.52, "lng": 13.40, "type": "cyber", "description": "Atak DDoS na infrastrukturę"}
        ],
        "sp50_trend": {"dates": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"], "prices": [4100, 4250, 4180, 4350, 4400, 4450]}
    }

    print(f"Starting update at {output_data['last_updated']}...")

    for category, urls in FEEDS.items():
        category_articles = []
        for url in urls:
            print(f"Fetching {url} for {category}...")
            try:
                fetched = fetch_feed(url)
                category_articles.extend(fetched)
            except Exception as e:
                print(f"Error fetching {url}: {e}")

        if category_articles:
            # Sort by date descending (newest first)
            try:
                # Use a safer way to parse dates in the sort key
                def get_date(art):
                    d_str = art['date'].replace('Z', '+00:00')
                    try:
                        return datetime.datetime.fromisoformat(d_str)
                    except:
                        return datetime.datetime.now()

                category_articles.sort(key=get_date, reverse=True)
            except Exception as e:
                print(f"Error sorting {category} articles: {e}")

        output_data[category] = category_articles

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
    print("Done! data.json updated.")

if __name__ == "__main__":
    main