/**
 * app.js - OSINT Dashboard Engine
 */

const DATA_URL = 'data.json';
const REFRESH_INTERVAL = 60000; // Refresh every 1 minute

document.addEventListener('DOMContentLoaded', () => {
    initMap();
    initChart();
    loadData();
    setInterval(loadData, REFRESH_INTERVAL);
});

async function loadData() {
    try {
        const response = await fetch(DATA_URL);
        if (!response.ok) throw new Error('Failed to load data');
        const data = await response.json();

        // 1. Update Last Updated Timestamp
        document.getElementById('update-time').textContent = data.last_updated;

        // 2. Update Critical Alert Banner
        const alertText = document.getElementById('critical-alert-text');
        if (data.critical_alerts && data.critical_alerts.length > 0) {
            alertText.textContent = data.critical_alerts[0];
        } else {
            alertText.textContent = "Sytuacja stabilna - brak nowych alarmów";
        }

        // 3. Render News (No Images, only Title + Snippet)
        renderNews('news-pl', data.poland);
        renderNews('news-world', [...data.world_security, ...data.world_politics]);
        renderNews('news-tech', data.technology);
        renderNews('news-cyber', data.cybersecurity);
        renderNews('news-finance', data.finance);

        // 4. Render Instability List
        renderInstability(data.instability);

        // 5. Update Map Layers
        updateMap(data.map_features);

        // 6. Update Chart
        if (data.sp50_trend) {
            updateChart(data.sp_500_trend || data.sp50_trend); // Robustness
        }

    } catch (err) {
        console.error('Error loading dashboard data:', err);
    }
}

function renderNews(containerId, articles) {
    const container = document.getElementById(containerId);
    if (!container || !articles) return;

    container.innerHTML = '';

    articles.forEach(article => {
        const card = document.createElement('div');
        card.className = 'p-3 bg-slate-900/50 border border-slate-800 rounded-lg hover:border-red-500 transition-all group';

        card.innerHTML = `
            <h3 class="text-sm font-bold text-slate-200 group-hover:text-red-400 leading-tight mb-1">${article.title}</h3>
            <p class="text-[11px] text-slate-500 line-clamp-3 leading-relaxed">${article.snippet}</p>
            <a href="${article.url}" target="_blank" class="text-[9px] uppercase font-black text-red-600 mt-2 inline-block opacity-70 group-hover:opacity-100">Source &rarr;</a>
        `;
        container.appendChild(card);
    });
}

function renderInstability(countries) {
    const container = document.getElementById('country-instability');
    if (!container || !countries) return;

    container.innerHTML = countries.map(c => `
        <div class="flex justify-between items-center text-xs py-1 border-b border-slate-800/50 last:border-0">
            <span class="text-slate-400">${c.name}</span>
            <span class="${c.score > 70 ? 'text-red-500 font-bold' : c.score > 40 ? 'text-orange-500' : 'text-green-500'}">${c.score}%</span>
        </div>
    `).join('');
}

function initMap() {
    map = L.map('map', { zoomControl: false }).setView([20, 0], 2);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap'
    }).addTo(map);
}

function updateMap(features) {
    if (!map || !features) return;
    // Clear existing markers (simplistic implementation for demo)
    map.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) map.removeLayer(layer);
    });

    features.forEach(f => {
        const color = f.type === 'war' ? '#ef444	_red_500' : '#f59e0b'; // orange-500
        const marker = L.circleMarker([f.lat, f.lng], {
            radius: 8,
            fillColor: color,
            color: "#fff",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.8
        }).addTo(map);
        marker.bindPopup(`<b>${f.type.toUpperCase()}</b><br>${f.description}`);
    });
}

function initChart() {
    const ctx = document.getElementById('sp500Chart').getContext('2d');
    sp500Chart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ data: [], borderColor: '#ef4444', tension: 0.4, borderWidth: 2, pointRadius: 0, fill: true, backgroundColor: 'rgba(239,68,68,0.1)' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { grid: { color: '#1e2template_293b' }, ticks: { font: { size: 8 } } } } }
    });
}

function updateChart(trend) {
    if (!sp500Chart || !trend) return;
    sp500Chart.data.labels = trend.dates;
    sp500Chart.data.datasets[0].data = trend.prices;
    sp500Chart.update();
}
