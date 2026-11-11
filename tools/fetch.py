import json, os, io, time, urllib.request, urllib.parse
from PIL import Image, ImageDraw, ImageFont, ImageOps
from concurrent.futures import ThreadPoolExecutor
Image.MAX_IMAGE_PIXELS = None
UA = {'User-Agent': 'VoiceTripBooth/1.0 (university student-day booth; github.com/eqht98-dotcom/voice-trip)'}
def get(url, raw=False, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                b = r.read(12_000_000)
                if raw and len(b) >= 12_000_000: print('  too big', url[:100]); return None
                return b if raw else json.loads(b)
        except Exception as e:
            err = e; time.sleep(2*(k+1))
    print('  fail', url[:120], err); return None
def commons(q, n=5):
    u = 'https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode({'action':'query','generator':'search','gsrsearch':q+' filetype:bitmap','gsrnamespace':6,'gsrlimit':12,'prop':'imageinfo','iiprop':'url|size|extmetadata','iiurlwidth':960,'format':'json'})
    d = get(u) or {}; out = []
    pages = sorted((d.get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 99))
    for p in pages:
        ii = (p.get('imageinfo') or [{}])[0]
        if ii.get('width', 0) < 900 or ii.get('height', 0) < 500: continue
        md = ii.get('extmetadata', {})
        out.append({'src':'commons','thumb':ii.get('thumburl'),'page':ii.get('descriptionurl'),'author':(md.get('Artist',{}).get('value') or '')[:200],'license':md.get('LicenseShortName',{}).get('value','')})
        if len(out) >= n: break
    return out
def wikilead(t):
    d = get('https://en.wikipedia.org/api/rest_v1/page/summary/' + urllib.parse.quote(t.replace(' ','_')))
    if d and d.get('originalimage'):
        o = d['originalimage']['source']; th = (d.get('thumbnail') or {}).get('source', o)
        th = th.replace('/320px-', '/960px-') if '/320px-' in th else th
        return [{'src':'wikipedia','thumb':th,'page':d.get('content_urls',{}).get('desktop',{}).get('page',''),'author':'','license':''}]
    return []
def openverse(q, n=4):
    d = get('https://api.openverse.org/v1/images/?' + urllib.parse.urlencode({'q':q,'page_size':10,'aspect_ratio':'wide','size':'large'})) or {}
    out = []
    for r in d.get('results', []):
        u = r.get('url') or ''
        if 'staticflickr.com' in u and u.endswith('.jpg') and not u[-6:-4].startswith('_'): u = u[:-4] + '_b.jpg'
        elif 'staticflickr.com' not in u: u = r.get('thumbnail') or u
        out.append({'src':'openverse:'+(r.get('source') or ''),'thumb':u,'page':r.get('foreign_landing_url'),'author':r.get('creator') or '','license':(r.get('license') or '')+' '+(r.get('license_version') or '')})
        if len(out) >= n: break
    return out
def cover(b, W=800, H=600):
    im = Image.open(io.BytesIO(b)); im = ImageOps.exif_transpose(im).convert('RGB')
    return ImageOps.fit(im, (W, H), Image.LANCZOS)
items = json.load(open('tools/items.json'))
os.makedirs('cand', exist_ok=True); meta = {}
def work(it):
    key = f"{it['code']}_{it['sec']}_{it['i']}"
    cands = wikilead(it['wiki']) + commons(it['q']) + openverse(it['q'])
    seen = set(); kept = []
    for c in cands:
        if not c.get('thumb') or c['thumb'] in seen: continue
        seen.add(c['thumb'])
        b = get(c['thumb'], raw=True, tries=2)
        if not b: continue
        try: im = cover(b)
        except Exception as e: print('  bad image', e); continue
        n = len(kept); im.save(f'cand/{key}_{n}.jpg', quality=84, optimize=True, progressive=True)
        kept.append(c)
        if len(kept) >= 8: break
    print(key, it['q'], len(kept), flush=True)
    return key, {'item': it, 'cands': kept}
with ThreadPoolExecutor(6) as ex:
    for key, v in ex.map(work, items): meta[key] = v
json.dump(meta, open('cand/meta.json','w'), ensure_ascii=False, indent=1)
# contact sheets per country: rows = items, columns = candidates
try: font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 20)
except Exception: font = ImageFont.load_default()
TW, TH = 260, 195
for code in dict.fromkeys(i['code'] for i in items):
    keys = [k for k in meta if k.startswith(code+'_')]
    sheet = Image.new('RGB', (230 + 8*(TW+6), len(keys)*(TH+6)+6), (20,24,32)); d = ImageDraw.Draw(sheet)
    for r, k in enumerate(keys):
        y = 6 + r*(TH+6); d.text((8, y+8), k.split('_',1)[1], fill=(240,220,160), font=font)
        d.text((8, y+40), meta[k]['item']['wiki'][:18], fill=(200,200,200), font=font)
        for c in range(len(meta[k]['cands'])):
            t = Image.open(f'cand/{k}_{c}.jpg').resize((TW, TH)); x = 230 + c*(TW+6); sheet.paste(t, (x, y))
            d.rectangle([x, y, x+30, y+28], fill=(0,0,0)); d.text((x+8, y+3), str(c), fill=(255,255,0), font=font)
    sheet.save(f'cand/sheet_{code}.jpg', quality=80)
print('done')
