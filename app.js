/**
 * app.js - OSINT Dashboard Engine
 */

const DATA_URL = 'data.json';
const REFRESH_INTERVAL = 60000; // 60s - dashboard refreshes itself, no reload needed
const FRESH_THRESHOLD_MINUTES = 30;
const MAX_ARTICLES_PER_CATEGORY = 10;
const MAX_ARTICLES_PER_SUBCATEGORY = 5;

let map, sp500Chart;
let mapMarkers = [];
let activeLayers = { critical: true, conflict: true, cyber: true, disaster: true, gps_jamming: true };

const LAYER_COLORS = {
    critical: '#ec4899',
    conflict: '#ef4444',
    cyber: '#a855f7',
    disaster: '#f59e0b',
    gps_jamming: '#eab308',
};

document.addEventListener('DOMContentLoaded', () => {
    initMap();
    initChart();
    initLayerToggles();
    loadData();
    setInterval(loadData, REFRESH_INTERVAL);
    setTimeout(() => map && map.invalidateSize(), 100);
    window.addEventListener('resize', () => map && map.invalidateSize());
});

function initLayerToggles() {
    document.querySelectorAll('#map-layers input[type="checkbox"]').forEach((box) => {
        box.addEventListener('change', () => {
            activeLayers[box.dataset.layer] = box.checked;
            renderMapMarkers();
        });
    });
}

function isFresh(dateStr) {
    const d = new Date(dateStr);
    if (isNaN(d)) return false;
    const diffMinutes = (Date.now() - d.getTime()) / 60000;
    return diffMinutes >= 0 && diffMinutes <= FRESH_THRESHOLD_MINUTES;
}

const articleDateFormatter = new Intl.DateTimeFormat('pl-PL', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Warsaw',
});

function formatArticleDate(dateStr) {
    const d = new Date(dateStr);
    if (isNaN(d)) return '';
    return articleDateFormatter.format(d);
}

async function loadData() {
    try {
        const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error('Failed to load data');
        const data = await response.json();

        const timeEl = document.getElementById('update-time');
        if (timeEl) {
            const d = new Date(data.last_updated);
            timeEl.textContent = isNaN(d) ? data.last_updated : d.toLocaleString('pl-PL', { timeZone: 'Europe/Warsaw' });
        }

        updateCriticalTicker(data.critical_alerts);

        renderNews('news-pl', data.poland);

        const worldContainer = document.getElementById('news-world');
        if (worldContainer) {
            worldContainer.innerHTML = '';
            worldContainer.appendChild(buildSubcategoryColumn('news-world-security', 'Bezpieczeństwo', 'text-red-500', data.world_security));
            worldContainer.appendChild(buildSubcategoryColumn('news-world-politics', 'Polityka', 'text-blue-500', data.world_politics));
        }

        renderNews('news-tech', data.technology);
        renderNews('news-cyber', data.cybersecurity);
        renderNews('news-finance', data.finance);

        renderInstability(data.instability);
        renderInvestmentPicks(data.investment_picks);

        mapMarkers = data.map_features || [];
        renderMapMarkers();

        if (data.sp500_trend) updateChart(data.sp500_trend);

    } catch (err) {
        console.error('Error loading dashboard data:', err);
    }
}

function updateCriticalTicker(alerts) {
    const track = document.getElementById('critical-ticker');
    const a = document.getElementById('critical-alert-text-a');
    const b = document.getElementById('critical-alert-text-b');
    if (!track || !a || !b) return;
    const text = (alerts && alerts.length ? alerts : ['Sytuacja stabilna - brak nowych alarmów krytycznych'])
        .join('   •   ');
    a.textContent = text;
    b.textContent = text;
    // Slow, constant reading speed regardless of how much text there is -
    // longer alert lists get a longer loop instead of scrolling faster.
    track.style.animationDuration = `${Math.max(25, text.length * 0.22)}s`;
}

function buildSubcategoryColumn(id, label, colorClass, articles) {
    const wrapper = document.createElement('div');
    wrapper.className = 'news-world-column min-h-0 flex-1 flex flex-col gap-1 overflow-hidden';
    const header = document.createElement('h4');
    header.className = `text-[10px] font-bold uppercase ${colorClass} mb-0.5 shrink-0`;
    header.textContent = label;
    wrapper.appendChild(header);
    const div = document.createElement('div');
    div.id = id;
    div.className = 'min-h-0 flex-1 flex flex-col gap-2';
    renderNewsToElement(div, articles, MAX_ARTICLES_PER_SUBCATEGORY);
    wrapper.appendChild(div);
    return wrapper;
}

function renderNews(containerId, articles) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    renderNewsToElement(container, articles, MAX_ARTICLES_PER_CATEGORY);
}

function renderNewsToElement(container, articles, limit) {
    if (!container || !articles) return;
    const list = limit ? articles.slice(0, limit) : articles;
    list.forEach((article) => {
        const fresh = isFresh(article.date);
        const card = document.createElement('div');
        card.className = `news-card${fresh ? ' new-article' : ''}`;
        const confirmedBadge = article.confirmed_by > 1
            ? `<span class="confirmed-badge">Potwierdzone przez ${article.confirmed_by} źródła</span>`
            : '';
        card.innerHTML = `
            <h3 class="news-title">${article.title}${confirmedBadge}</h3>
            <p class="news-snippet">${article.summary}</p>
            <div class="news-footer">
                <a href="${article.url}" target="_blank" rel="noopener noreferrer" class="news-link">${article.source || 'Źródło'} &rarr;</a>
                <span class="news-date">${formatArticleDate(article.date)}</span>
            </div>
        `;
        container.appendChild(card);
    });
}

function renderInstability(countries) {
    const container = document.getElementById('country-instability');
    if (!container) return;
    if (!countries || countries.length === 0) {
        container.innerHTML = '<p class="text-slate-500 text-xs">Brak danych o niestabilności.</p>';
        return;
    }
    container.innerHTML = countries.map((c) => `
        <div class="instability-item">
            <span class="text-slate-400">${c.name}</span>
            <span class="${c.score > 70 ? 'risk-high' : c.score > 40 ? 'risk-med' : 'risk-low'}">${c.score}%</span>
        </div>
    `).join('');
}

function renderInvestmentPicks(picks) {
    const container = document.getElementById('investment-picks');
    if (!container) return;
    if (!picks || picks.length === 0) {
        container.innerHTML = '<p class="text-slate-500 text-xs">Brak sygnałów inwestycyjnych w bieżących wydarzeniach.</p>';
        return;
    }
    container.innerHTML = picks.map((p) => `
        <div class="border-b border-slate-800/50 last:border-0 pb-1">
            <div class="text-emerald-400 font-bold text-[10px]">${p.sector}</div>
            <div class="text-slate-500 text-[9px]">${p.instrument}</div>
        </div>
    `).join('');
}

function initMap() {
    map = L.map('map', { zoomControl: false }).setView([20, 0], 2);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap'
    }).addTo(map);
}

function renderMapMarkers() {
    if (!map) return;
    map.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) map.removeLayer(layer);
    });
    mapMarkers.forEach((f) => {
        if (!activeLayers[f.type]) return;
        const color = LAYER_COLORS[f.type] || '#ef4444';
        const marker = L.circleMarker([f.lat, f.lng], {
            radius: 8,
            fillColor: color,
            color: '#fff',
            weight: 1,
            opacity: 1,
            fillOpacity: 0.85,
        }).addTo(map);
        const link = f.url ? `<br><a href="${f.url}" target="_blank" rel="noopener noreferrer" style="color:${color}">Czytaj &rarr;</a>` : '';
        marker.bindPopup(`<b>${f.region || f.type.toUpperCase()}</b><br>${f.description}${link}`);
    });
}

const sp500LabelFormatter = new Intl.DateTimeFormat('pl-PL', { day: '2-digit', month: '2-digit' });
const sp500PlnFormatter = new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 0 });

function initChart() {
    const ctx = document.getElementById('sp500Chart').getContext('2d');
    sp500Chart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ data: [], borderColor: '#3b82f6', tension: 0.3, borderWidth: 2, pointRadius: 0, fill: true, backgroundColor: 'rgba(59,130,246,0.1)' }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    display: true,
                    grid: { display: false },
                    ticks: { font: { size: 7 }, color: '#64748b', maxTicksLimit: 6, maxRotation: 0 },
                },
                y: {
                    position: 'left',
                    display: true,
                    grid: { color: '#1e293b' },
                    ticks: {
                        font: { size: 7 },
                        color: '#64748b',
                        maxTicksLimit: 4,
                        callback: (value) => `${sp500PlnFormatter.format(value)} zł`,
                    },
                },
            },
        },
    });
}

function updateChart(trend) {
    if (!sp500Chart || !trend || !trend.dates || !trend.dates.length) return;
    sp500Chart.data.labels = trend.dates.map((d) => sp500LabelFormatter.format(new Date(d)));
    sp500Chart.data.datasets[0].data = trend.prices;
    sp500Chart.update();

    const currentEl = document.getElementById('sp500-current');
    const changeEl = document.getElementById('sp500-change');
    const prices = trend.prices;
    const last = prices[prices.length - 1];
    if (currentEl) currentEl.textContent = `${sp500PlnFormatter.format(last)} zł`;
    if (changeEl && prices.length > 1) {
        const prev = prices[prices.length - 2];
        const diffPct = ((last - prev) / prev) * 100;
        changeEl.textContent = `${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(2)}%`;
        changeEl.className = `text-[10px] font-bold mt-0.5 ${diffPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`;
    }
}
