"""Fetch each country's cinematic clips from Pixabay and prepare them for the site.

Runs in GitHub Actions (see .github/workflows/clips.yml). Needs the PIXABAY_API_KEY secret.
For every country in clips/queries.json it searches Pixabay videos, prefers portrait videos
(landscape ones are cropped to the middle), takes the best unused result per search, and writes
clips/<code>_<n>.mp4 at 720x1280, 24 fps, no B-frames (the page reads frames in order with the
browser's video decoder). It also writes a contact sheet per country (clips/_preview/<code>.jpg)
and the credits (clips/credits.json). A slot can be "id:12345" to use one exact Pixabay video.
"""
import json, os, subprocess, sys, urllib.request, urllib.parse, tempfile

KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
if not KEY:
    sys.exit("PIXABAY_API_KEY secret is missing")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = os.path.join(ROOT, "clips")
PREVIEW = os.path.join(CLIPS, "_preview")
os.makedirs(PREVIEW, exist_ok=True)
queries = json.load(open(os.path.join(CLIPS, "queries.json")))
only = [c for c in os.environ.get("ONLY", "").split(",") if c]
credits_path = os.path.join(CLIPS, "credits.json")
credits = json.load(open(credits_path)) if os.path.exists(credits_path) else {}

# a clip only counts for a country when its Pixabay tags name that country or one of its places
PLACES = {
    "es": ["spain", "spanish", "españa", "tenerife", "canary", "canarias", "mallorca", "majorca", "pyrenees", "andalusia", "andalucia", "ibiza", "menorca", "picos de europa", "gran canaria", "lanzarote"],
    "gb": ["scotland", "scottish", "highlands", "skye", "lake district", "wales", "cornwall", "dover", "england", "united kingdom", "britain", "uk", "glencoe", "snowdonia"],
    "fr": ["france", "french", "provence", "lavender", "corsica", "normandy", "etretat", "étretat", "chamonix", "mont blanc", "verdon", "brittany", "alsace"],
    "de": ["germany", "german", "bavaria", "bavarian", "black forest", "saxon", "zugspitze", "königssee", "koenigssee", "konigssee", "rhine", "harz", "eibsee"],
    "it": ["italy", "italian", "dolomites", "dolomiti", "como", "tuscany", "toscana", "sicily", "sardinia", "garda", "amalfi", "cinque terre", "positano", "capri"],
    "tr": ["turkey", "turkish", "türkiye", "turkiye", "cappadocia", "pamukkale", "antalya", "fethiye", "oludeniz", "ölüdeniz", "kas", "bodrum"],
    "ru": ["russia", "russian", "baikal", "altai", "kamchatka", "siberia", "karelia", "caucasus", "elbrus", "murmansk"],
    "in": ["india", "indian", "kerala", "ladakh", "munnar", "kashmir", "goa", "himachal", "uttarakhand", "rajasthan", "meghalaya", "sikkim"],
    "cn": ["china", "chinese", "zhangjiajie", "guilin", "yunnan", "huangshan", "li river", "jiuzhaigou", "yangshuo", "great wall"],
    "jp": ["japan", "japanese", "fuji", "hokkaido", "kyoto", "nikko", "hakone", "okinawa", "sakura"],
    "kr": ["korea", "korean", "jeju", "seoraksan", "busan", "gangwon"],
    "id": ["indonesia", "indonesian", "bali", "raja ampat", "bromo", "lombok", "komodo", "java", "sumatra", "flores", "nusa penida"],
    "no": ["norway", "norwegian", "lofoten", "tromso", "tromsø", "geiranger", "preikestolen", "senja", "trolltunga", "fjord", "reine"],
}
used_everywhere = set(queries.get("_skip", []))

def search(q):
    params = {"key": KEY, "per_page": 60, "safesearch": "true", "video_type": "film"}
    if q.startswith("id:"):
        params["id"] = q[3:]
    else:
        params["q"] = q
    req = urllib.request.Request("https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params), headers={"User-Agent": "voice-trip-clips"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get("hits", [])

def best_file(hit):
    # the version closest to 1920 on its long side; portrait videos come first when choosing between hits
    files = [v for v in (hit.get("videos") or {}).values() if v.get("url") and v.get("width") and v.get("height")]
    if not files:
        return None
    return min(files, key=lambda v: abs(max(v["width"], v["height"]) - 1920))

def rank(hit):
    f = best_file(hit)
    portrait = f and f["height"] > f["width"]
    return (0 if portrait else 1)

def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

report = []
for code, slots in queries.items():
    if code.startswith("_") or not isinstance(slots, list) or (only and code not in only):
        continue
    used, made = set(), []
    for n, q in enumerate(slots, 1):
        try:
            hits = search(q)
            places = PLACES.get(code, [])
            words = [w for w in q.lower().split() if len(w) > 2]
            def score(h):
                tags = (h.get("tags") or "").lower()
                place = any(p in tags for p in places)
                landmark = sum(w in tags for w in words)
                return (0 if place else 1, -landmark, rank(h))
            ok = [h for h in hits if h["id"] not in used and h["id"] not in used_everywhere and h.get("duration", 0) >= 6 and best_file(h)]
            if not q.startswith("id:"):
                ok = [h for h in ok if any(p in (h.get("tags") or "").lower() for p in places)]
            ok.sort(key=score)
            pick = ok[0] if ok else None
            if not pick:
                report.append(f"{code}_{n}: nothing found for '{q}'")
                continue
            used.add(pick["id"]); used_everywhere.add(pick["id"])
            f = best_file(pick)
            with tempfile.NamedTemporaryFile(suffix=".mp4") as src:
                req = urllib.request.Request(f["url"], headers={"User-Agent": "voice-trip-clips"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    src.write(r.read()); src.flush()
                out = os.path.join(CLIPS, f"{code}_{n}.mp4")
                run(["ffmpeg", "-y", "-i", src.name, "-t", "16", "-an",
                     "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "29", "-profile:v", "main", "-bf", "0", "-g", "48",
                     "-movflags", "+faststart", out])
            credits[f"{code}_{n}"] = {"query": q, "pixabay_id": pick["id"], "url": pick.get("pageURL"), "by": pick.get("user"), "duration": pick.get("duration"), "portrait": f["height"] > f["width"]}
            made.append(out)
            report.append(f"{code}_{n}: [{pick.get('tags')}] {pick.get('pageURL')} ({pick.get('duration')}s, {'portrait' if f['height'] > f['width'] else 'landscape, cropped'})")
        except Exception as e:  # keep going with the other clips
            report.append(f"{code}_{n}: error {e}")
    # remove leftover clips beyond the ones made now
    for n in range(len(slots) + 1, 7):
        p = os.path.join(CLIPS, f"{code}_{n}.mp4")
        if os.path.exists(p):
            os.remove(p)
    if made:
        thumbs = []
        for i, m in enumerate(made):
            t = os.path.join(PREVIEW, f"_t{i}.jpg")
            run(["ffmpeg", "-y", "-ss", "2", "-i", m, "-frames:v", "1", "-vf", "scale=240:-1", t])
            thumbs.append(t)
        args = ["ffmpeg", "-y"] + sum([["-i", t] for t in thumbs], []) + ["-filter_complex", f"hstack=inputs={len(thumbs)}" if len(thumbs) > 1 else "null", os.path.join(PREVIEW, f"{code}.jpg")]
        run(args)
        for t in thumbs:
            os.remove(t)

json.dump(credits, open(credits_path, "w"), ensure_ascii=False, indent=1)
open(os.path.join(PREVIEW, "report.txt"), "w").write("\n".join(report) + "\n")
print("\n".join(report))
