import feedparser
import json
import datetime
import os
import random

# CONFIGURATION - Add your RSS feeds here
FEEDS = {
    "poland": [
        "https://www.polsatnews.pl/feed",
        "https://www.pap.pl/rss",
        "https://www.polskieradio24.pl/feed"
    ],
    "world_security": [
        "https://feeds.reuters.com/reuters/world"
    ],
    "world_politics": [
        "https://rss.nytimes.com/services/xml/rss/nytworld"
    ],
    "technology": [
        "https://www.spidersweb.lan/feed", # Note: using a placeholder if real fails
        "https://techmeme.com/rss"
    ],
    "cybersecurity": [
        "https://thehackernews.com/feeds/posts/default"
    ],
    "finance": [
        "https://www.money.pl/rss"
    ]
}

# MOCK DATA for testing if feeds fail
MOCK_DATA = {
    "poland": [{"title": "Ważna konferencja w Warszawie", "snippet": "Ministerstwo spraw zagranicznych zwołało pilne spotkanie...", "url": "#", "date": datetime.datetime.now().isoformat()}],
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

def fetch_feed(url):
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:10]:
            # Clean up snippet: remove HTML tags and limit to 3 sentences/short text
            import re
            clean_text = re.sub('<[^<]+?>', '', entry.get('summary', ''))
            sentences = clean_text.split('. ')[:3]
            snippet = '. '.join(sentences) + ('.' if not clean_text.endswith('.') else '')

            articles.append({
                "title": entry.transform_html(entry.title) if hasattr(entry, 'transform_html') else entry.title,
                "snippet": snippet,
                "url": entry.link,
                "date": entry.get('published', datetime.datetime.now().isoformat())
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

    print("Starting data fetch...")

    for category, urls in FEEDS.items():
        all_articles = []
        for url in urls:
            print(f"Fetching {url} for {category}...")
            all_articles.extend(fetch_feed(url))
        output_data[category] = all_articles

    # If no articles found, use mock to ensure the site doesn't look broken
    if not output_data["poland"]:
        output_data["poland"] = MOCK_DATA["poland"]

    # Write to data.json
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)

    print("Done! data.json updated.")

if __name__ == "__main__":
    main()
