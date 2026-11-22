#!/usr/bin/env python3
"""
F9 Baltic XML_Interface -> kruza.lv XML feed.

Same output format as the old f9.py (f9_catalog.xml): <products><product> with
kods, ean, nosaukums, razotajs, noliktava, cena, kategorija, serija, akcija,
so the existing import profile keeps working. Extra fields are added on top:
  kategorija_serija   "<ClassID> / <Series>" : finer category for mapping
  noliktava_<id>      stock per warehouse
  grp_<GroupID>       every Grouping/GroupBy value
  <other tags>        any other simple field the API returns for Product/Price

Credentials come only from environment variables (GitHub Secrets):
  F9_USER, F9_PASS, F9_CLIENT_ID, F9_KEY

Usage:
  python3 f9_feed.py --probe            prints the structure of the API answer (no file written)
  python3 f9_feed.py public/f9.xml      writes the feed (keeps the old file if the new one is much smaller)

Optional filters (env, default off = same content as the old file):
  F9_ONLY_EAN=1        drop products without EAN
  F9_SKIP_CATS=WAR,SOF drop these ClassID codes (services, licences...)
  F9_CAT_NUMBER=1001   catalog number for CatalogRequest

Only standard library, Python 3.8+.
"""
import collections, hashlib, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

URL = os.environ.get('F9_URL', 'http://www.f9baltic.com/scripts/XML_Interface.dll')
USER = os.environ.get('F9_USER', '')
PASS = os.environ.get('F9_PASS', '')
CLIENT_ID = os.environ.get('F9_CLIENT_ID', '')
KEY = os.environ.get('F9_KEY', '')
CAT_NUMBER = os.environ.get('F9_CAT_NUMBER', '1001')
ONLY_EAN = os.environ.get('F9_ONLY_EAN', '0') == '1'
SKIP_CATS = {c.strip().upper() for c in os.environ.get('F9_SKIP_CATS', '').split(',') if c.strip()}
MIN_SHARE = 0.5   # keep the old file if the new one has < 50% of its products


def fetch(body=None, timeout_default='1200'):
    if not (USER and PASS and CLIENT_ID and KEY):
        sys.exit('F9_USER / F9_PASS / F9_CLIENT_ID / F9_KEY are not set')
    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    body = body or f"""<CatalogRequest>
  <Date>{now}</Date>
  <CatNumber>{CAT_NUMBER}</CatNumber>
  <Route>
    <From><ClientID>{CLIENT_ID}</ClientID></From>
    <To><ClientID>1</ClientID></To>
  </Route>
  <Filters>
    <Filter FilterID="Price" Value="VAT"/>
  </Filters>
</CatalogRequest>"""
    check = hashlib.md5((body + KEY).encode()).hexdigest()
    data = urllib.parse.urlencode({'USERNAME': USER, 'PASSWORD': PASS, 'XML': body, 'CHECK': check}).encode()
    req = urllib.request.Request(URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    timeout = int(os.environ.get('F9_TIMEOUT', timeout_default))
    try:  # which IP F9 sees (helps if F9 has an IP whitelist)
        with urllib.request.urlopen('https://api.ipify.org', timeout=15) as r:
            print('Runner public IP:', r.read().decode())
    except Exception as e:
        print('Could not detect runner IP:', e)
    last = None
    for attempt in range(2):
        t0 = datetime.now()
        try:
            print(f'F9 request, attempt {attempt + 1}, timeout {timeout}s', flush=True)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                print(f'F9 answered HTTP {r.status} after {(datetime.now() - t0).seconds}s', flush=True)
                chunks, total = [], 0
                while True:
                    c = r.read(1 << 20)
                    if not c:
                        break
                    chunks.append(c)
                    total += len(c)
                    print(f'  received {total:,} bytes', flush=True)
                return b''.join(chunks)
        except Exception as e:  # network hiccup or blocked: retry once
            last = e
            print(f'  failed after {(datetime.now() - t0).seconds}s: {e!r}', flush=True)
    sys.exit(f'F9 request failed: {last}')


def load_names(fn):
    import csv
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ref', fn)
    try:
        with open(path, encoding='utf-8', newline='') as f:
            return {r['code']: r['name'] for r in csv.DictReader(f)}
    except OSError:
        return {}


GROUPS = load_names('groups.csv')   # ClassID code -> product group name (from F9 reference file)
VENDORS = load_names('vendors.csv')  # VendorID code -> vendor name


def tag(name):
    t = re.sub(r'[^0-9a-zA-Z_]+', '_', name).strip('_').lower()
    return t if t and not t[0].isdigit() else 'f_' + t


def probe(root):
    """Print every element path with count and a sample value, so we see what the API gives."""
    paths, samples, attrs = collections.Counter(), {}, collections.defaultdict(set)
    def walk(el, path):
        p = f'{path}/{el.tag}'
        paths[p] += 1
        txt = (el.text or '').strip()
        if txt and p not in samples:
            samples[p] = txt[:80]
        for k, v in el.attrib.items():
            if len(attrs[p]) < 15:
                attrs[p].add(f'{k}={v[:40]}')
        for c in el:
            walk(c, p)
    walk(root, '')
    print('ROOT:', root.tag, dict(root.attrib))
    for p, n in paths.items():
        print(f'{n:>7}  {p}  {samples.get(p, "")!r}  {sorted(attrs.get(p, []))[:15]}')
    first = root.find('.//CatalogItem')
    if first is not None:
        print('\nFIRST ITEM:\n' + ET.tostring(first, encoding='unicode')[:4000])
    if first is None:
        print('\nNO CatalogItem. Start of answer:\n' + ET.tostring(root, encoding='unicode')[:3000])


def parse(root):
    rows = []
    for item in root.iter('CatalogItem'):
        prod = item.find('Product')
        if prod is None:
            continue
        r = collections.OrderedDict()
        r['kods'] = (prod.findtext('PartNumber') or '').strip()
        r['ean'] = (prod.findtext('EANCode') or '').strip()
        r['nosaukums'] = (prod.findtext('Description') or '').strip()
        grp = {}
        for g in prod.findall('Grouping/GroupBy'):
            grp[g.get('GroupID', '')] = (g.get('Value') or '').strip()
        r['razotajs'] = grp.get('VendorID', '')
        qtys = collections.OrderedDict()
        for q in item.findall('Qty'):
            wh = q.get('WarehouseID') or q.findtext('WarehouseID') or q.findtext('Warehouse') or str(len(qtys) + 1)
            try:
                qtys[wh] = int(float(q.findtext('QtyAvailable') or 0))
            except ValueError:
                qtys[wh] = 0
        r['noliktava'] = sum(qtys.values())
        r['cena'] = (item.findtext('Price/UnitPrice') or '').strip()
        r['kategorija'] = grp.get('ClassID', '')
        r['serija'] = grp.get('Series', '')
        # all price types F9 sends (C = customer; L1..L3 = price levels), kept in separate fields
        for up in item.findall('Price/UnitPrice'):
            t = (up.get('Type') or '').strip()
            if t and (up.text or '').strip():
                r['cena_' + tag(t)] = up.text.strip()
        qd = item.find('Price/QtyDiscount')
        if qd is not None:
            r['atlaide_no_daudzuma'] = (qd.findtext('MinQty') or '').strip()
            r['atlaide_cena'] = (qd.findtext('DiscPrice') or '').strip()
        dd = [(q.findtext('DeliveryDate') or '').strip() for q in item.findall('Qty')]
        dd = [d for d in dd if d]
        if dd:
            r['piegade_datums'] = min(dd)
        r['akcija'] = '1' if (item.findtext('Price/PromoFlag') or '0').strip() == '1' else ''
        r['kategorija_nosaukums'] = GROUPS.get(r['kategorija'], '')
        r['razotajs_nosaukums'] = VENDORS.get(r['razotajs'], '')
        r['kategorija_serija'] = f"{r['kategorija']} / {r['serija']}" if r['serija'] else r['kategorija']
        for wh, n in qtys.items():
            r['noliktava_' + tag(wh)] = n
        for gid, v in grp.items():
            r['grp_' + tag(gid)] = v
        # any other simple fields (pictures, weight, RRP...) that the API may return
        for parent in (prod, item.find('Price')):
            if parent is None:
                continue
            for c in parent:
                if len(c) == 0 and c.tag not in ('PartNumber', 'EANCode', 'Description', 'UnitPrice', 'PromoFlag'):
                    k = tag(c.tag)
                    if k not in r and (c.text or '').strip():
                        r[k] = c.text.strip()
        if ONLY_EAN and not r['ean']:
            continue
        if r['kategorija'].upper() in SKIP_CATS:
            continue
        rows.append(r)
    return rows


def write(rows, out):
    old = 0
    if os.path.exists(out):
        with open(out, 'rb') as f:
            old = f.read().count(b'<product>')
    if old and len(rows) < old * MIN_SHARE:
        sys.exit(f'New feed has {len(rows)} products vs {old} in the old one: old file kept')
    root = ET.Element('products', generated=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'), count=str(len(rows)))
    for r in rows:
        p = ET.SubElement(root, 'product')
        for k, v in r.items():
            ET.SubElement(p, k).text = str(v)
    ET.indent(root, space='  ')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + '.tmp'
    ET.ElementTree(root).write(tmp, encoding='utf-8', xml_declaration=True)
    os.replace(tmp, out)
    print(f'Written {out}: {len(rows)} products (old file had {old})')


def spec_body(part, lang):
    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return f'''<ProductSpecRequest>
  <Date>{now}</Date>
  <Route>
    <From><ClientID>{CLIENT_ID}</ClientID></From>
    <To><ClientID>1</ClientID></To>
  </Route>
  <Language>{lang}</Language>
  <PartNumber>{part}</PartNumber>
</ProductSpecRequest>'''


def spec_probe(parts, langs):
    for part in parts:
        for lang in langs:
            print(f'===== {part} / {lang}')
            raw = fetch(spec_body(part, lang), '120')
            print(raw[:6000].decode('utf-8', 'replace') if b'\x00' not in raw[:200] else raw[:200])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--specprobe':
        parts = (sys.argv[2] if len(sys.argv) > 2 else '21Q000D1MH').split(',')
        spec_probe(parts, (sys.argv[3] if len(sys.argv) > 3 else 'LAT,ENG').split(','))
        return
    raw = fetch()
    print(f'Answer: {len(raw):,} bytes')
    try:
        root = ET.fromstring(raw)  # bytes: expat reads encoding="Windows-1257" itself
    except ET.ParseError as e:
        print(raw[:1500].decode('latin-1', 'replace'))
        sys.exit(f'XML parse error: {e}')
    if len(sys.argv) > 1 and sys.argv[1] == '--probe':
        probe(root)
        return
    rows = parse(root)
    if not rows:
        print(raw[:1500].decode('latin-1', 'replace'))
        sys.exit('No products in the answer')
    write(rows, sys.argv[1] if len(sys.argv) > 1 else 'f9.xml')


if __name__ == '__main__':
    main()
