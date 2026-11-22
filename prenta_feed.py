#!/usr/bin/env python3
"""
Prenta API -> kruza.lv XML feed (same schema as Proks/Leno/Asbis).

Runs on a server by cron and writes a STATIC xml file into a public folder.
The XML import profile on kruza.lv (Automātiskais grafiks) reads that URL.

API (docs.prenta.lt):
  /products            list: id, name, internal_reference, category_id, date_edited
  /products/{id}       full card: barcode (EAN), images, manufacturer_id, price_rrp, weight, description...
  /prices              product_id, price, gross_price
  /stock_levels        product_id, qty, forecast_date
  /categories          id, name, parent_id
  /manufacturers       id, name
Full cards are cached in a json file and re-fetched only for new products or when
date_edited changed, so a normal run is ~a few dozen requests, not thousands.

Usage:
  PRENTA_USER=... PRENTA_PASS=... python3 prenta_feed.py --probe
  PRENTA_USER=... PRENTA_PASS=... python3 prenta_feed.py /var/www/kruza/feeds/prenta-<secret>.xml

Cron (every 4 hours, ~20 min before the import schedule):
  40 */4 * * * PRENTA_USER=... PRENTA_PASS=... /usr/bin/python3 /opt/feeds/prenta_feed.py /var/www/kruza/feeds/prenta-<secret>.xml >> /var/log/prenta_feed.log 2>&1

Only standard library, Python 3.8+.
"""
import base64, json, os, re, sys, time, urllib.request, urllib.error
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

API = os.environ.get('PRENTA_API', 'https://e.prenta.lt/api/v1')
USER = os.environ.get('PRENTA_USER', '')
PASS = os.environ.get('PRENTA_PASS', '')
CACHE = os.environ.get('PRENTA_CACHE', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prenta_cache.json'))
PER_PAGE = 100
MIN_SHARE = 0.5          # keep the old file if new one has < 50% of its products
ONLY_IN_STOCK = os.environ.get('PRENTA_ONLY_IN_STOCK', '0') == '1'
# Optional language for names/categories, e.g. PRENTA_LANG=lv_LV (Prenta runs on Odoo, LV is installed
# on their site). Not documented in the API; only set it after prenta_lang_probe.html shows it works.
LANG = os.environ.get('PRENTA_LANG', '')

# LT product-type word -> LV (keyed by Prenta category name). Names from the API look like
# "<LT type> <BRAND> <MODEL>"; we swap the LT type for the LV one. Unknown category -> name stays as is.
try:
    TYPE_LV = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prenta_type_lv.json'), encoding='utf-8'))
except (OSError, ValueError):
    TYPE_LV = {}


def lv_name(name, cat_lt):
    lv = TYPE_LV.get(cat_lt)
    if lv and cat_lt and name.lower().startswith(cat_lt.lower()):
        return lv + name[len(cat_lt):]
    return name


AUTH = 'Basic ' + base64.b64encode(f'{USER}:{PASS}'.encode()).decode()


def get(path, params=None, retries=4):
    url = API + path
    params = dict(params or {})
    if LANG:
        params['lang'] = LANG
    if params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
    headers = {'Authorization': AUTH, 'Accept': 'application/json'}
    if LANG:
        headers['Accept-Language'] = LANG.replace('_', '-')
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(5 * (attempt + 1)); continue
            raise
        except urllib.error.URLError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f'API not responding: {url}')


def all_pages(path):
    out, page = [], 1
    while True:
        data = get(path, {'page': page, 'per_page': PER_PAGE}) or []
        out += data
        if len(data) < PER_PAGE:
            return out
        page += 1
        time.sleep(0.1)


def num(v):
    try:
        return str(Decimal(str(v).replace(',', '.')).normalize()) if v not in (None, '') else ''
    except (InvalidOperation, ValueError):
        return ''


def probe():
    for path in ['/products', '/prices', '/stock_levels', '/categories', '/manufacturers']:
        print(f'===== {path}')
        print(json.dumps(get(path, {'page': 1, 'per_page': 2}), indent=2, ensure_ascii=False)[:2500])
    first = (get('/products', {'page': 1, 'per_page': 1}) or [{}])[0]
    if first:
        print(f'===== /products/{first["id"]}')
        print(json.dumps(get(f'/products/{first["id"]}'), indent=2, ensure_ascii=False)[:4000])


def build(out_path):
    t0 = time.time()
    products = all_pages('/products')
    prices = {p['product_id']: p for p in all_pages('/prices')}
    stock, forecast = {}, {}
    for s in all_pages('/stock_levels'):
        stock[s['product_id']] = stock.get(s['product_id'], 0) + int(Decimal(str(s.get('qty') or 0)))
        if s.get('forecast_date'):
            forecast[s['product_id']] = s['forecast_date']
    cats = {c['id']: c for c in all_pages('/categories')}
    mans = {m['id']: m.get('name', '') for m in all_pages('/manufacturers')}

    # full cards: cached, refreshed only when new / date_edited changed
    try:
        cache = json.load(open(CACHE, encoding='utf-8'))
    except (OSError, ValueError):
        cache = {}
    fetched = 0
    for p in products:
        key = str(p['id'])
        if key not in cache or cache[key].get('date_edited') != p.get('date_edited'):
            card = get(f'/products/{p["id"]}')
            if card:
                cache[key] = card; fetched += 1
                if fetched % 200 == 0:
                    json.dump(cache, open(CACHE, 'w', encoding='utf-8'))  # progress survives a crash
            time.sleep(0.05)
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'))

    # Output format = the one the existing kruza.lv profile "Prenta XML (FULL)" (id 13) is mapped to:
    # catalog/product with id, name, sku, ean, supplier_code, price, cost_price, stock, category,
    # brand, description, unit, image, forecast_date. Category = Prenta's own (LT) category name,
    # which is what the profile's category map + per-category markups are keyed on.
    L = ['<?xml version="1.0" encoding="UTF-8"?>', '<catalog>']
    n = no_price = 0
    for p in products:
        card = cache.get(str(p['id']), {})
        pr = prices.get(p['id'], {})
        price = num(pr.get('price'))                      # purchase price, excl. VAT
        if not price:
            no_price += 1; continue
        qty = stock.get(p['id'], 0)
        if ONLY_IN_STOCK and qty <= 0:
            continue
        ean = re.sub(r'\s', '', str(card.get('barcode') or ''))
        imgs = card.get('images') or []
        cat_lt = (cats.get(p.get('category_id')) or {}).get('name', '')
        raw_name = str(card.get('compound_name') or card.get('name') or p.get('name') or '')
        f = [('id', str(p['id'])),
             ('name', lv_name(raw_name, cat_lt)),
             ('sku', str(p.get('internal_reference') or '')),
             ('ean', ean if re.fullmatch(r'\d{8}|\d{12,14}', ean) else ''),
             ('supplier_code', str(p.get('internal_reference') or '')),
             ('price', price),
             ('cost_price', price),
             ('stock', str(max(qty, 0))),
             ('category', cat_lt),
             ('brand', mans.get(card.get('manufacturer_id'), '')),
             ('description', str(card.get('description') or '')),
             ('unit', 'gab.'),
             ('image', str(imgs[0]) if imgs else ''),
             ('forecast_date', str(forecast.get(p['id']) or ''))]
        L.append('  <product>')
        L += [f'    <{k}>{escape(v)}</{k}>' for k, v in f]
        L.append('  </product>')
        n += 1
    L.append('</catalog>')

    if os.path.exists(out_path):
        old = open(out_path, encoding='utf-8').read().count('<product>')
        if old and n < old * MIN_SHARE:
            sys.exit(f'{time.ctime()} ABORT: {n} products vs {old} in current file, file kept')
    tmp = out_path + '.tmp'
    open(tmp, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    os.replace(tmp, out_path)  # atomic: import never reads a half-written file
    print(f'{time.ctime()} OK: {n} products in XML, {no_price} without price, '
          f'{len(stock)} with stock rows, {fetched} cards refreshed, {time.time()-t0:.0f}s')


if __name__ == '__main__':
    if not USER or not PASS:
        sys.exit('set PRENTA_USER and PRENTA_PASS')
    if len(sys.argv) > 1 and sys.argv[1] == '--probe':
        probe()
    elif len(sys.argv) > 1:
        build(sys.argv[1])
    else:
        sys.exit(__doc__)
