function seriesSummary(series) {
    const coverage = series.catalogued ? `${series.owned_episodes}/${series.aired_episodes} aired episodes in library` : 'Episode catalogue not loaded';
    return `<div class="muted">TV series · ${escapeHtml(coverage)} ${series.id ? `<a href="#/series/${series.id}">Seasons & episodes →</a>` : ''}</div>`;
}

function seriesCard(series) {
    return `<article class="tv-card"><h3>${escapeHtml(series.name)} ${series.year ? `(${series.year})` : ''}</h3>${seriesSummary(series)}</article>`;
}

async function loadSeriesPage(identifier) {
    let page = document.getElementById('pageSeries');
    if (!page) {
        page = document.createElement('section');
        page.id = 'pageSeries';
        page.className = 'page';
        document.getElementById('app').appendChild(page);
    }
    page.classList.add('active');
    page.innerHTML = '<h2>TV series</h2><p role="status">Loading…</p>';
    try {
        const response = await fetch('/api/series' + (identifier ? '/' + encodeURIComponent(identifier) : ''));
        if (!response.ok) throw new Error('Could not load the series catalogue.');
        const data = await response.json();
        if (!page.classList.contains('active')) return;
        if (!identifier) {
            page.innerHTML = '<h2>TV series</h2><form id="tvAdd"><input name="title" required placeholder="Find a series" aria-label="Series title"><input name="year" type="number" placeholder="Premiere year" aria-label="Premiere year"><input name="tvmaze_id" type="number" placeholder="TVmaze ID (optional)" aria-label="TVmaze ID"><button class="btn">Load catalogue</button><span id="tvAddStatus" role="status"></span></form><input id="tvSearch" placeholder="Filter series" aria-label="Filter series"><div id="tvCatalogue"></div>';
            document.getElementById('tvAdd').onsubmit = async event => {
                event.preventDefault();
                const form = new FormData(event.target);
                const status = document.getElementById('tvAddStatus');
                status.textContent = ' Looking up series…';
                try {
                    const response = await fetch('/api/series/catalogue', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: form.get('title'), year: Number(form.get('year')) || null, tvmaze_id: Number(form.get('tvmaze_id')) || null})});
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail);
                    location.hash = '/series/' + result.id;
                } catch (error) { status.textContent = error.message; }
            };
            const render = () => {
                const q = document.getElementById('tvSearch').value.toLowerCase();
                document.getElementById('tvCatalogue').innerHTML = data.filter(s => s.name.toLowerCase().includes(q)).map(seriesCard).join('') || '<p>No series indexed yet. Episode filenames such as S01E01 are recognized during scanning.</p>';
            };
            document.getElementById('tvSearch').addEventListener('input', render);
            render();
            return;
        }
        const seasons = [...new Set(data.episodes.map(e => e.season))];
        page.innerHTML = `<a href="#/series">← All series</a>${seriesCard(data)}
            <button class="btn" id="tvRefresh">Refresh episode catalogue</button><span id="tvNotice" role="status"></span>
            ${data.source_url ? `<p>Episode metadata: <a href="${escapeHtml(data.source_url)}" target="_blank" rel="noopener">TVmaze</a> · CC BY-SA</p>` : ''}
            <div id="tvSeriesActions"></div>
            ${seasons.map(number => `<section class="tv-season" data-season="${number}"><h3>Season ${number}</h3><div class="tv-season-actions"></div>${data.episodes.filter(e => e.season === number).map(e => `<div class="tv-episode" data-episode="${e.number}"><span>S${String(number).padStart(2, '0')}E${String(e.number).padStart(2, '0')}</span><span>${escapeHtml(e.title || 'Title unknown')}<small>${escapeHtml(e.airdate || 'Airdate unknown')} · ${e.runtime || '?'} min</small></span><span>${e.movie_ids.length ? `<a href="#/movie/${e.movie_ids[0]}">Watch / resume →</a>` : e.aired ? 'Missing' : 'Not aired / unconfirmed'}</span><span class="tv-episode-actions"></span></div>`).join('')}</section>`).join('')}`;
        document.getElementById('tvRefresh').onclick = async () => {
            const notice = document.getElementById('tvNotice');
            notice.textContent = ' Updating catalogue…';
            try {
                const res = await fetch('/api/series/catalogue', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: data.title, year: data.year, tvmaze_id: data.tvmaze_id})});
                const result = await res.json();
                if (!res.ok) throw new Error(result.detail);
                loadSeriesPage(result.id);
            } catch (error) { notice.textContent = error.message; }
        };
        document.dispatchEvent(new CustomEvent('series-rendered', {detail: {series: data, page}}));
    } catch (error) { page.innerHTML = `<p role="alert">${escapeHtml(error.message)}</p>`; }
}

// Keep playback, ratings, screenshots, subtitles, and history attached to their existing video IDs.
let episodeRoute = '';
async function showEpisodeNavigation() {
    const match = location.hash.match(/^#\/movie\/(\d+)/);
    if (!match || episodeRoute === location.hash) return;
    episodeRoute = location.hash;
    const route = episodeRoute;
    try {
        const response = await fetch(`/api/series/file/${match[1]}`);
        if (!response.ok) return;
        const data = await response.json();
        if (!data || location.hash !== route) return;
        let navigation = document.getElementById('tvEpisodeNavigation');
        if (!navigation) {
            navigation = document.createElement('div');
            navigation.id = 'tvEpisodeNavigation';
            document.getElementById('pageMovieDetails').prepend(navigation);
        }
        const next = data.next_episode;
        navigation.innerHTML = `<a href="#/series/${data.series.id}">${escapeHtml(data.series.title)} · Seasons & episodes</a> · ${data.episodes.map(e => `S${e.season}E${e.number}: ${escapeHtml(e.title || '')}`).join(', ')} ${next ? next.movie_ids.length ? ` · <a href="#/movie/${next.movie_ids[0]}">Next episode →</a>` : ' · Next episode is not in the library' : ''}`;
    } catch (error) { console.error('Episode navigation:', error); }
}
window.addEventListener('hashchange', () => {
    document.getElementById('tvEpisodeNavigation')?.remove();
    episodeRoute = '';
    showEpisodeNavigation();
});
window.addEventListener('load', showEpisodeNavigation);
