// Cloudflare Worker: وسيط آمن يحمل مفاتيح ElevenLabs وClaude ولا يكشفها للزوار.
// Secrets (Settings > Variables and Secrets): ELEVENLABS_KEY, ANTHROPIC_KEY
// KV namespace binding: LIMITS
const ALLOW_ORIGIN = 'https://eqht98-dotcom.github.io';
const TRIPS_PER_DAY = 3;      // رحلات لكل جهاز/IP باليوم (تُحسب عند إنشاء نسخة الصوت)
const CALLS_PER_DAY = 60;     // إجمالي الطلبات لكل IP باليوم
const GLOBAL_TRIPS = 200;     // سقف الرحلات اليومي لكل الزوار
const CLAUDE_MODEL = 'claude-sonnet-5-5';
const PIN_HASH = 'e1455f44cbeb2ab3da7f3ba62deeb4e6e08ea090641f3b3313104053e4f306c5'; // same PIN as the page
const PIN_TRIES_PER_DAY = 20; // wrong PINs per IP per day
const BOOTH_DAYS = 30;        // a device unlocked with the PIN skips the per-visitor limits for this long

const cors = o => ({
  'Access-Control-Allow-Origin': o,
  'Access-Control-Allow-Headers': 'content-type,x-booth',
  'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
  'Vary': 'Origin'
});
const j = (o, s, h) => new Response(JSON.stringify(o), {status: s, headers: Object.assign({'content-type': 'application/json'}, h)});

async function bump(kv, key, limit) {
  const n = parseInt((await kv.get(key)) || '0', 10);
  if (n >= limit) return false;
  await kv.put(key, String(n + 1), {expirationTtl: 90000});
  return true;
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get('Origin') || '';
    const okOrigin = origin === ALLOW_ORIGIN;
    const h = cors(ALLOW_ORIGIN);
    if (req.method === 'OPTIONS') return new Response(null, {status: 204, headers: h});
    if (!okOrigin) return j({error: 'forbidden'}, 403, h);

    const url = new URL(req.url);
    const p = url.pathname;
    const ip = req.headers.get('CF-Connecting-IP') || 'x';
    const day = new Date().toISOString().slice(0, 10);
    // the booth's own devices (opened once with the PIN) carry a token; every visitor there shares one IP
    const bt = req.headers.get('x-booth') || '';
    const booth = /^[a-f0-9]{32}$/.test(bt) && !!(await env.LIMITS.get('booth:' + bt));

    // ---- organisers' settings, shared by every device that knows the PIN ----
    // the PIN is checked here (same hash as the page); wrong PINs are limited per IP and day
    if (p === '/sync' && req.method === 'POST') {
      let b; try { b = await req.json(); } catch (e) { return j({error: 'bad'}, 400, h); }
      const tries = `pin:${day}:${ip}`;
      const n = parseInt((await env.LIMITS.get(tries)) || '0', 10);
      if (n >= PIN_TRIES_PER_DAY) return j({error: 'limit', detail: 'pin_tries'}, 429, h);
      const d = new TextEncoder().encode('voice-trip:' + String(b.pin || ''));
      const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', d))].map(x => x.toString(16).padStart(2, '0')).join('');
      if (hash !== PIN_HASH) { await env.LIMITS.put(tries, String(n + 1), {expirationTtl: 90000}); return j({error: 'pin'}, 403, h); }
      const token = crypto.randomUUID().replace(/-/g, '');
      await env.LIMITS.put('booth:' + token, '1', {expirationTtl: BOOTH_DAYS * 86400});
      if (b.data !== undefined) {
        const body = JSON.stringify({t: Date.now(), data: b.data});
        if (body.length > 200000) return j({error: 'too_big'}, 413, h);
        await env.LIMITS.put('sync:v2', body);
        return j({ok: true, t: JSON.parse(body).t, booth: token}, 200, h);
      }
      const saved = await env.LIMITS.get('sync:v2');
      return j(Object.assign(saved ? JSON.parse(saved) : {t: 0, data: null}, {booth: token}), 200, h);
    }

    // ---- remaining ElevenLabs credits, for the balance at the top of the page ----
    // only the number left is returned (never the key or the account details), and it is not counted
    // in the daily call limit
    if (p === '/credits' && req.method === 'GET') {
      const r = await fetch('https://api.elevenlabs.io/v1/user/subscription', {headers: {'xi-api-key': env.ELEVENLABS_KEY}});
      if (!r.ok) return j({error: 'upstream', status: r.status}, 502, h);
      const s = await r.json();
      return j({left: (s.character_limit ?? 0) - (s.character_count ?? 0)}, 200, h);
    }

    if (!booth && !(await bump(env.LIMITS, `c:${day}:${ip}`, CALLS_PER_DAY)))
      return j({error: 'limit', detail: 'daily_calls'}, 429, h);

    // ---- Claude ----
    if (p === '/claude' && req.method === 'POST') {
      let b; try { b = await req.json(); } catch (e) { return j({error: 'bad'}, 400, h); }
      b.model = CLAUDE_MODEL;
      b.max_tokens = Math.min(b.max_tokens || 2500, 3000);
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {'x-api-key': env.ANTHROPIC_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
        body: JSON.stringify(b)
      });
      return new Response(r.body, {status: r.status, headers: Object.assign({'content-type': 'application/json'}, h)});
    }

    // ---- ElevenLabs ----
    if (p.startsWith('/el/')) {
      const sub = p.slice(3); // يبدأ بـ /
      const allowed =
        (sub === '/speech-to-text' && req.method === 'POST') ||
        (sub === '/voices' && req.method === 'GET') ||   // voice library list, for the melody site (same origin)
        (sub === '/voices/add' && req.method === 'POST') ||
        (/^\/voices\/[\w-]+$/.test(sub) && req.method === 'DELETE') ||
        (/^\/text-to-speech\/[\w-]+\/with-timestamps$/.test(sub) && req.method === 'POST');
      if (!allowed) return j({error: 'not_allowed'}, 403, h);

      if (sub === '/voices/add') {
        if (!booth && !(await bump(env.LIMITS, `t:${day}:${ip}`, TRIPS_PER_DAY)))
          return j({error: 'limit', detail: 'trips_per_day'}, 429, h);
        if (!(await bump(env.LIMITS, `g:${day}`, GLOBAL_TRIPS)))
          return j({error: 'limit', detail: 'global'}, 429, h);
      }
      const r = await fetch('https://api.elevenlabs.io/v1' + sub + url.search, {
        method: req.method,
        headers: {'xi-api-key': env.ELEVENLABS_KEY, 'content-type': req.headers.get('content-type') || ''},
        body: ['GET', 'DELETE'].includes(req.method) ? undefined : req.body
      });
      const rh = new Headers(h);
      const ct = r.headers.get('content-type'); if (ct) rh.set('content-type', ct);
      return new Response(r.body, {status: r.status, headers: rh});
    }
    return j({error: 'not_found'}, 404, h);
  }
};
