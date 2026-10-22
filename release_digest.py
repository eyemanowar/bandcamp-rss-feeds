"""Release-only daily RSS for a public Bandcamp profile. No login required."""
import concurrent.futures
import html
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

PROFILE = 'eyemanowar'
STATE = Path('release-state.json')
FEED = Path('feed.xml')
UA = 'Mozilla/5.0 (X11; U; Linux i686) Gecko/20071127 Firefox/2.0.0.11'
lock = threading.Lock()
last_request = 0.0


def fetch(url, payload=None):
    global last_request
    for attempt in range(4):
        with lock:
            time.sleep(max(0, last_request + 2.0 - time.monotonic()))
            last_request = time.monotonic()
        cmd = ['curl', '--fail', '--silent', '--show-error', '--location',
               '--max-time', '40', '--write-out', '\n%{http_code}', '--user-agent', UA, url]
        if payload is not None:
            cmd += ['--data', json.dumps(payload)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        body, _, status = result.stdout.rpartition('\n')
        if result.returncode == 0 and status.startswith('2'):
            return body
        if status == '429' or status.startswith('5') or status == '000':
            # Share the cooldown across workers; do not keep hitting rate limits.
            with lock:
                last_request = max(last_request, time.monotonic() + 60 * (attempt + 1))
            if attempt < 3:
                continue
        raise RuntimeError(f'HTTP {status}: {result.stderr.strip()[:160]} ({url})')
    raise RuntimeError(f'Request retries exhausted: {url}')


def following():
    soup = BeautifulSoup(fetch(f'https://bandcamp.com/{PROFILE}'), 'html.parser')
    page = soup.select_one('#pagedata')
    if page is None:
        raise RuntimeError('Bandcamp profile unavailable or challenged; preserving existing state.')
    blob = json.loads(page['data-blob'])
    fan_id = blob['fan_data']['fan_id']
    expected = blob['fan_data']['following_bands_count']
    bands = {}
    token = '9999999999:9999999999'
    while True:
        response = json.loads(fetch('https://bandcamp.com/api/fancollection/1/following_bands',
            {'fan_id': fan_id, 'older_than_token': token, 'count': 1000}))
        for band in response['followeers']:
            slug = band['url_hints']['subdomain']
            if not slug or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in slug):
                raise RuntimeError('Invalid Bandcamp subdomain')
            bands[str(band['band_id'])] = {'name': band['name'], 'url': f'https://{slug}.bandcamp.com/music'}
        if not response['more_available']:
            break
        next_token = response['last_token']
        if not next_token or next_token == token:
            raise RuntimeError('Following pagination did not advance')
        token = next_token
    if len(bands) < expected:
        raise RuntimeError(f'Incomplete following list: {len(bands)} of {expected}')
    return bands


def catalogue(band):
    soup = BeautifulSoup(fetch(band['url']), 'html.parser')
    # Bandcamp sometimes returns a plain HTML redirect for a single-release artist.
    if soup.get_text().startswith('You are being redirected'):
        target = soup.select_one('a[href]')
        url = urljoin(band['url'], target['href']) if target else ''
        parsed = urlparse(url)
        if parsed.scheme == 'https' and parsed.hostname == urlparse(band['url']).hostname:
            soup = BeautifulSoup(fetch(url), 'html.parser')
    if not soup.title or any(s in soup.title.get_text().lower() for s in ('client challenge', 'just a moment')):
        raise RuntimeError('Bandcamp returned a challenge')
    releases = {}
    for a in soup.select('ol.music-grid li.music-grid-item a, .ipCellLabel1 a'):
        url = urljoin(band['url'], a.get('href', ''))
        if urlparse(url).path.startswith(('/album/', '/track/')):
            title = a.select_one('.title') or a
            releases[url] = ' '.join(title.stripped_strings) or url.rsplit('/', 1)[-1]
    # Some artists redirect /music to their sole release.
    if not releases and soup.select_one('.trackTitle'):
        canonical = soup.select_one('link[rel="canonical"], meta[property="og:url"]')
        if canonical:
            url = canonical.get('href') or canonical.get('content')
            if urlparse(url).path.startswith(('/album/', '/track/')):
                title = soup.select_one('.trackTitle')
                releases[url] = title.get_text(strip=True) if title else soup.title.get_text()
    if not releases and not soup.select_one('#music-grid, ol.music-grid'):
        raise RuntimeError('No recognizable music catalogue; preserving previous baseline')
    return releases


def write_feed(state, now):
    root = ET.Element('rss', version='2.0')
    channel = ET.SubElement(root, 'channel')
    for tag, value in [('title', f'Bandcamp releases for {PROFILE}'),
                       ('link', f'https://bandcamp.com/{PROFILE}'),
                       ('description', 'Daily new releases from followed artists and labels.'),
                       ('lastBuildDate', format_datetime(now, usegmt=True))]:
        ET.SubElement(channel, tag).text = value
    for digest in reversed(state['digests'][-180:]):
        item = ET.SubElement(channel, 'item')
        n = len(digest['releases'])
        ET.SubElement(item, 'title').text = f"{digest['day']}: {n} new Bandcamp release{'s' if n != 1 else ''}"
        ET.SubElement(item, 'link').text = f'https://bandcamp.com/{PROFILE}'
        ET.SubElement(item, 'guid', isPermaLink='false').text = f'bandcamp:{PROFILE}:{digest["day"]}'
        ET.SubElement(item, 'pubDate').text = digest['pubDate']
        rows = [f'<li><a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"])}</a> — {html.escape(r["source"])}</li>' for r in digest['releases']]
        ET.SubElement(item, 'description').text = '<ul>' + ''.join(rows) + '</ul>'
    if not state['digests']:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = 'Tracking started — waiting for new releases'
        ET.SubElement(item, 'link').text = f'https://bandcamp.com/{PROFILE}'
        ET.SubElement(item, 'guid', isPermaLink='false').text = f'bandcamp:{PROFILE}:tracking-started'
        ET.SubElement(item, 'pubDate').text = state['started']
        ET.SubElement(item, 'description').text = 'Existing catalogues are being baselined. New releases will appear here after subsequent daily checks.'
    ET.indent(root)
    ET.ElementTree(root).write(FEED, encoding='utf-8', xml_declaration=True)


def main():
    now = datetime.now(timezone.utc)
    state = json.loads(STATE.read_text()) if STATE.exists() else {
        'started': format_datetime(now, usegmt=True), 'bands': {}, 'digests': [], 'seen': []}
    bands = following()
    seen = set(state['seen'])
    changes = {}
    failures = []
    print(f'Checking {len(bands)} followed artists and labels', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        jobs = {executor.submit(catalogue, band): (band_id, band) for band_id, band in bands.items()}
        for index, future in enumerate(concurrent.futures.as_completed(jobs), 1):
            band_id, band = jobs[future]
            try:
                releases = future.result()
                previous = state['bands'].get(band_id)
                if previous is not None:
                    for url, title in releases.items():
                        if url not in previous['releases'] and url not in seen:
                            changes[url] = {'url': url, 'title': title, 'source': band['name']}
                state['bands'][band_id] = {**band, 'releases': sorted(set(releases) | set(previous['releases'] if previous else []))}
                seen.update(releases)
            except Exception as error:
                failures.append(band['url'])
                print(f'::warning::{band["name"]}: {str(error)[:200]}', flush=True)
            if index % 25 == 0:
                print(f'Checked {index}/{len(bands)}', flush=True)
    if len(failures) == len(bands) and bands:
        raise RuntimeError('All catalogue checks failed; no state or feed written')
    # Only retain baselines for current follows. Previously seen URLs prevent duplicates.
    state['bands'] = {k: v for k, v in state['bands'].items() if k in bands}
    if changes:
        day = now.date().isoformat()
        if state['digests'] and state['digests'][-1]['day'] == day:
            digest = state['digests'][-1]
            existing = {r['url'] for r in digest['releases']}
            digest['releases'].extend(r for url, r in changes.items() if url not in existing)
        else:
            state['digests'].append({'day': day, 'pubDate': format_datetime(now, usegmt=True), 'releases': list(changes.values())})
    state['digests'] = state['digests'][-180:]
    state['seen'] = sorted(seen)
    state['last_checked'] = now.isoformat()
    state['failed_catalogues'] = failures
    write_feed(state, now)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    summary = f'Checked {len(bands)} follows; {len(changes)} new releases; {len(failures)} catalogue failures.'
    print(summary, flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary)


if __name__ == '__main__':
    main()
