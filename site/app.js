/* The Worry Theatre — front end for 1,000 Worriers. Plain JS + GSAP, no build step.
   Same-origin API behind CloudFront: POST /runs, GET /runs/{id}, GET /runs/{id}/worries?limit=N
   Every number shown comes from the API (or, in ?mock=N test mode, from clearly labelled mock data). */
(() => {
  "use strict";
  const C = window.Creatures;
  const G = window.gsap;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const D = (s) => (reduce ? 0.01 : s);
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 }); // counts and per-1,000 values: whole numbers only
  const cleanFix = (s) => String(s ?? "").replace(/^(me|i)\s*:\s*/i, "");
  const cap = (s) => String(s ?? "").replace(/^./, (m) => m.toUpperCase());
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const wait = (ms) => new Promise((r) => setTimeout(r, reduce ? Math.min(ms, 300) : ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const seqOf = (w) => parseInt(String(w.seq).replace(/\D/g, ""), 10);
  const WANT = "what the people affected actually want";
  const THEME_COLORS = ["#FF3D71", "#FB923C", "#FACC15", "#8B5CF6", "#22D3EE"];

  const DEMOS = {
    proposal: "I'm proposing to my girlfriend of four years on Saturday evening, 6:30 PM, at Elliot's Beach in Chennai at sunset. Her best friend will bring her there saying it's a surprise birthday dinner for someone else. Twelve friends will hide behind the food stalls and come out with candles when she says yes. A friend will record it on his phone. The ring is with me. Afterwards, dinner for everyone at a restaurant nearby, booked for 8 PM. Her parents don't know yet; I plan to tell them on Sunday.",
    breakup: "I need to end a 2-year relationship kindly. We share a flat lease ending in March, a dog, and the same friend group. I plan to talk to him on Sunday at home.",
    cutover: "We're cutting over our production PostgreSQL database to a new Aurora cluster this Saturday, 2:00–2:40 AM IST. Writes are paused via a feature flag; we have a rollback plan and a snapshot from Friday night. Two engineers on the call; the on-call engineer joined last month. Customer support starts at 7 AM. The mobile app caches tokens for 24 hours.",
    launch: "We're launching our budgeting app's new subscription tier on Tuesday at 10 AM IST. Pricing page and in-app paywall go live together behind a feature flag. Email to 40,000 users scheduled for 10:15. Two engineers and one support person on shift. App Store review for the iOS update was submitted on Friday. Payments via Razorpay; a 30% launch discount code for the first week.",
    nightout: "Eight college friends meeting for the first time in five years, Saturday night in Bengaluru. Rooftop bar at 8 PM, then a late dinner, then whoever is up goes for a drive to Nandi Hills for sunrise. Two people are flying in that afternoon. We'll split the bill on an app.",
    purchase: "Buying my first car next week: a used hatchback, five years old, 48,000 km, from a seller I found online. Paying 4.2 lakh, half from savings and half from a personal loan that's approved but not disbursed. Test drive Saturday, payment and RC transfer Monday.",
    trip: "Family trip to Munnar, Dec 20-24. Six people including two kids (6 and 9). Overnight train Chennai to Ernakulam on Dec 19, then a hired taxi up the ghat road to Munnar, arriving evening Dec 20. Homestay booked and paid. Mostly paying by UPI, little cash. Return by the same taxi and train on Dec 24.",
    monsoon: "Household plan for a heavy-rain week in Chennai. Ground-floor flat. Elderly neighbour upstairs who lives alone. Important documents in a low cupboard. One two-wheeler. One phone charger, no power bank. Kids still have school. Food for about three days at home.",
  };

  /* ================= sound (WebAudio only, off by default) ================= */
  const Sound = {
    on: false, ctx: null, last: 0,
    ensure() { if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)(); if (this.ctx.state === "suspended") this.ctx.resume(); },
    tone(f, dur, type = "sine", vol = 0.06, slide = 1) {
      if (!this.on) return;
      this.ensure();
      const t = this.ctx.currentTime, o = this.ctx.createOscillator(), g = this.ctx.createGain();
      o.type = type; o.frequency.setValueAtTime(f, t);
      if (slide !== 1) o.frequency.exponentialRampToValueAtTime(f * slide, t + dur);
      g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g).connect(this.ctx.destination); o.start(t); o.stop(t + dur + 0.03);
    },
    pop() { const n = performance.now(); if (n - this.last < 70) return; this.last = n; this.tone(rand(480, 760), 0.09, "sine", 0.06, 1.7); },
    gasp() {
      if (!this.on) return; this.ensure();
      const t = this.ctx.currentTime, len = 0.28, buf = this.ctx.createBuffer(1, Math.floor(this.ctx.sampleRate * len), this.ctx.sampleRate);
      const d = buf.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
      const s = this.ctx.createBufferSource(), f = this.ctx.createBiquadFilter(), g = this.ctx.createGain();
      f.type = "bandpass"; f.frequency.setValueAtTime(900, t); f.frequency.exponentialRampToValueAtTime(2400, t + len);
      g.gain.setValueAtTime(0.09, t); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      s.buffer = buf; s.connect(f).connect(g).connect(this.ctx.destination); s.start(t);
    },
    chime() { [880, 1175, 1568].forEach((f, i) => setTimeout(() => this.tone(f, 0.6, "triangle", 0.045), i * 110)); },
    tick() { this.tone(700, 0.12, "square", 0.025, 1.5); },
  };

  /* ================= API (real, or mock with ?mock=N) ================= */
  const Real = {
    async req(method, path, body) {
      const res = await fetch(path, { method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
      if (res.status === 429) throw new Error("The worriers are booked up for today (daily limit reached). Please try again tomorrow.");
      if (res.status === 404) { const e = new Error("not found"); e.code = 404; throw e; }
      if (!res.ok) throw new Error(`Something went wrong (HTTP ${res.status}). Please try again.`);
      return res.json();
    },
    start(body) { return this.req("POST", "runs", body); },
    run(id) { return this.req("GET", `runs/${encodeURIComponent(id)}`); },
    worries(id, limit = 30) { return this.req("GET", `runs/${encodeURIComponent(id)}/worries?limit=${limit}`); },
    async revise(id, body) {
      const res = await fetch(`runs/${encodeURIComponent(id)}/revise`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || out.error) throw new Error("Couldn't build the revised plan. Please try again.");
      return out;
    },
    async reminders(id, body) {
      const res = await fetch(`runs/${encodeURIComponent(id)}/reminders`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out.cause || out.error || `HTTP ${res.status}`);
      return out;
    },
  };

  function makeMock(defaultN) {
    const groups = ["the person proposed to", "her parents", "the twelve hiding friends", "the restaurant staff", "people on the beach", "the planner", "her best friend", "local weather watchers", "the friend filming"];
    const names = [["her private side", "her romantic side", "her as a daughter"], ["her mother", "her father", "her aunt"], ["the friend with candles", "the late friend", "the giggly friend"],
      ["the restaurant host", "a busy waiter", "the cashier"], ["an evening walker", "a kite seller", "a curious kid"], ["the planner, nervous", "the planner, overconfident", "the planner, after"],
      ["her best friend", "her best friend, fibbing", "her best friend, late"], ["a weather nerd", "a fisherman", "a lifeguard"], ["the friend filming"]];
    const chars = []; names.forEach((ns, gi) => ns.forEach((n) => chars.push({ name: n, group: groups[gi] })));
    const angles = ["timing", "the ring", "secrecy", "the crowd", "weather", "recording", "dinner", WANT];
    const moments = ["a week before", "the hour before", "the main moment", "right after", "the next day"];
    const themes = [["T1", "Friends leak the surprise", "Someone tells her early."], ["T2", "Weather spoils the beach", "Rain, wind or fog."], ["T3", "Ring trouble", "Lost, dropped or forgotten."],
      ["T4", "Parents find out first", "Her parents hear before Sunday."], ["T5", "Recording goes wrong", "Phone dies or misses it."], ["T6", "Dinner booking problems", "Table lost or late."], ["T7", "Crowd at the beach", "Too many people around."]];
    const fails = {
      T1: ["A hiding friend posts a story and she sees it.", "Her best friend's smile gives the game away.", "Someone replies to the wrong group chat."],
      T2: ["Sudden drizzle sends everyone running.", "Wind blows out every candle at once.", "Sea fog hides the sunset completely."],
      T3: ["The ring box squeaks loudly in the quiet moment.", "Ring slips into the sand mid-kneel.", "Ring left at home on the dresser."],
      T4: ["Her mother calls right as he kneels.", "Her father hears from a neighbour first.", "Her aunt spots the group and phones home."],
      T5: ["The filming friend's phone dies at 1%.", "Video is recorded sideways and blurry.", "He forgets to press record."],
      T6: ["Restaurant gives the table away at 8:10.", "Twelve people, table for ten.", "Kitchen closes before everyone arrives."],
      T7: ["A cricket match crowd fills the beach.", "Strangers start filming too.", "Hawkers crowd the hiding spot."],
    };
    const runs = new Map();
    function build(body) {
      const id = "mock-" + Math.random().toString(36).slice(2, 10);
      const parent = body.parent_run_id ? runs.get(body.parent_run_id) : null;
      let n = parent ? parent.n : Math.min(1000, body.n || defaultN);
      let items;
      if (parent) items = parent.items;
      else {
        const pairs = []; chars.forEach((c) => angles.forEach((a) => pairs.push({ c, a })));
        pairs.sort(() => Math.random() - 0.5);
        const want = pairs.filter((p) => p.a === WANT).slice(0, 2), rest = pairs.filter((p) => p.a !== WANT);
        const chosen = [...want, ...rest, ...pairs.filter((p) => p.a === WANT).slice(2)].slice(0, Math.ceil(n / 5)).sort(() => Math.random() - 0.5);
        items = []; chosen.forEach((p) => moments.forEach((m) => items.push({ character: p.c.name, group: p.c.group, angle: p.a, moment: m })));
        items = items.slice(0, n).map((it, i) => ({ ...it, seq: i + 1 }));
        n = items.length; // like the real backend: N is what the plan's grid actually supports
      }
      const fixed = new Set(parent ? (body.accepted_fixes || []).map((f) => (f.match(/\[(T\d|Q|A)\]/) || [])[1]).filter(Boolean) : []);
      const worries = items.map((it) => {
        const r = Math.random();
        let t = r < 0.02 ? "offplan" : r < 0.05 ? "other" : themes[Math.min(themes.length - 1, Math.floor(Math.pow(Math.random(), 1.6) * themes.length))][0];
        let quiet = it.angle === WANT && Math.random() < 0.5;
        if (parent && (fixed.has(t) || Math.random() < 0.25)) t = Math.random() < 0.7 ? "other" : "T7";
        if (parent && fixed.has("Q") && quiet) quiet = Math.random() < 0.3;
        if (parent && fixed.has("A") && quiet) quiet = Math.random() < 0.4;
        const failure = quiet ? "She'd rather hear this in private, not in front of twelve people." : t === "offplan" ? "The hired band cancels." : t === "other" ? "Someone feels a bit awkward." : pick(fails[t]);
        return { ...it, failure, severity: quiet ? 5 : parent ? 1 + Math.floor(Math.random() * 3) : 2 + Math.floor(Math.random() * 4), early_sign: "Watch for it.", label: t, quiet };
      });
      const counts = {}; worries.forEach((w) => { counts[w.label] = (counts[w.label] || 0) + 1; });
      const ranked = themes.map(([tid, name, def]) => ({ id: tid, name, definition: def, count: counts[tid] || 0, avg_severity: 3.5 })).sort((a, b) => b.count - a.count);
      // mock evidence: real spans of the typed plan, so the highlight works like the real thing
      const ptxt = String(parent ? parent.plan.split("\n\nChanges made to the plan:")[0] : body.plan || "");
      const span = (k) => { const ws = ptxt.split(/\s+/).filter(Boolean); if (ws.length < 4) return []; const a = (k * 7) % Math.max(1, ws.length - 4); const q = ws.slice(a, a + 4).join(" "); return ptxt.includes(q) ? [q] : []; };
      const mcard = (t, k) => ({ failure: `Mock: ${t.name.toLowerCase()} on the day.`, mechanism: "Mock: a step in the plan → something slips → the moment suffers.", evidence: span(k),
        assumptions: ["Mock: the plan does not say who checks this"], assessment: k === 2 && span(k).length ? "contradicted" : span(k).length ? "supported" : "unverified",
        control_quote: k === 2 ? (span(k)[0] || "") : "", contradiction_reason: k === 2 ? "Mock: this part of the plan already prevents it." : "", question: `Mock: has someone checked ${t.name.toLowerCase()}?` });
      const mk = (t, k) => ({ ...t, fix: `[${t.id}] Mock fix for ${t.name.toLowerCase()}.`, owner: "me", fix_moment: "a week before", how_to_check: "Mock: confirm it in writing.", if_check_fails: "Mock: move it a day earlier.",
        backup: "Mock backup.", early_sign: "Mock early sign.", when: "2 days before", days_before: 2, examples: fails[t.id], card: mcard(t, k), grounding: { n: t.count, n_quoted: Math.round(t.count * 0.6), n_omission: Math.round(t.count * 0.2) } });
      const qws = worries.filter((w) => w.quiet);
      const quietSeq = (qws[0] || worries.find((w) => w.angle === WANT) || worries[0]).seq;
      const pq = parent && parent.result.quiet;
      const quiet = pq ? { ...pq, before: pq.count, count: qws.length } : { worry: "She'd rather hear this in private, not in front of twelve people.", question: "Does she even want this in public?", seq: quietSeq,
        character: items[quietSeq - 1].character, moment: items[quietSeq - 1].moment, severity: 5, count: Math.max(1, qws.length), fix: "[Q] Quietly ask her best friend whether she'd prefer a private moment.", backup: "Propose privately first; let friends join after.", early_sign: "She talks about hating being the centre of attention.", when: "a week before" };
      const wantN = worries.filter((w) => w.angle === WANT).length;
      const pa = parent && parent.result.affected;
      const affected = pa ? { ...pa, before: pa.count, of_before: pa.of, count: qws.length, of: wantN }
        : { worry: "She'd rather hear this in private, not in front of twelve people.", count: qws.length, of: wantN, seq: quietSeq, character: items[quietSeq - 1].character, moment: items[quietSeq - 1].moment,
            fix: "[A] Ask her best friend, honestly, whether she'd prefer a private moment; keep the surprise, never lie.", backup: "Propose privately first; friends join after.", early_sign: "She mentions hating big public moments.", when: "a week before", days_before: 7 };
      if (!pa) quiet.worry = "The restaurant table is booked under the wrong name.", quiet.question = "Is the dinner booking under the right name?", quiet.count = 3;
      if (!parent) { quiet.card = mcard({ name: "the quiet worry" }, 9); affected.card = mcard({ name: "what she wants" }, 5); affected.grounding = { n: affected.count, n_quoted: 1, n_omission: 2 };
        affected.how_to_check = "Mock: her best friend answers honestly."; affected.owner = "me"; quiet.how_to_check = "Mock: call the restaurant."; }
      const result = { n_worries: n, other: counts.other || 0, offplan: counts.offplan || 0, top5: ranked.slice(0, 5).map(mk), quiet, affected: wantN ? affected : null, themes: ranked,
        grounding: { quoted: Math.round(n * 0.6), omission: Math.round(n * 0.25), ungrounded: Math.round(n * 0.05), none: n - Math.round(n * 0.6) - Math.round(n * 0.25) - Math.round(n * 0.05) } };
      if (parent) {
        result.comparison = parent.result.themes.map((t) => ({ id: t.id, name: t.name, before: t.count, after: counts[t.id] || 0 }));
        result.severity_before = parent.worries.reduce((a, w) => a + w.severity, 0);
        result.severity_after = worries.reduce((a, w) => a + w.severity, 0);
        result.parent_run_id = parent.id;
        const changes = body.accepted_fixes || [], reps = body.planner_reports || [];
        const ofKey = (k) => (k === "affected" ? "A" : k === "quiet" ? "Q" : k);
        const keys = [...parent.result.top5.map((t) => t.id), ...(parent.result.affected ? ["affected"] : []), "quiet"];
        const nm = (k) => (k === "affected" ? "What she actually wants" : k === "quiet" ? "The quiet worry" : (parent.result.top5.find((t) => t.id === k) || {}).name);
        result.assessments = Object.fromEntries(keys.map((k) => {
          const ch = changes.find((c) => c.includes(`[${ofKey(k)}]`)), rp = reps.find((r) => r.startsWith(`${nm(k)}:`));
          return [k, ch ? { assessment: "addressed_in_plan", basis: ch, reason: "Mock: an accepted change covers this mechanism.", still_open: "Mock: do it and check it works." }
            : rp ? { assessment: "reported_covered_unverified", basis: "", reason: "Mock: only your own statement covers this.", still_open: "Mock: confirm it." }
            : { assessment: "unresolved", basis: "", reason: "Mock: nothing in the plan stops this yet.", still_open: "Mock: pick a fix." }];
        }));
        result.new_concerns = changes.length ? [{ concern: "Mock: the new step needs one more person to know.", mechanism: "Mock: more people know → more chances to slip.", related_change: changes[0] }] : [];
        result.noise_checked = false;
      }
      const run = { id, n, items, worries, result, parent, t0: performance.now(), wall: Date.now(), grid: { characters: chars, angles, moments },
        plan: parent ? parent.plan + "\n\nChanges made to the plan:\n- " + (body.accepted_fixes || []).join("\n- ") : body.plan,
        labels: Object.fromEntries(worries.map((w) => [String(w.seq), w.label])) };
      runs.set(id, run);
      return run;
    }
    function state(r) {
      const el = (performance.now() - r.t0) / 1000;
      const swarmSecs = Math.max(6, r.n / 70);
      if (el < 2.6 && !r.parent) return { status: "GRID", done: 0 };
      const s0 = r.parent ? 0.8 : 2.6;
      const done = Math.max(0, Math.min(r.n, Math.floor(((el - s0) / swarmSecs) * r.n)));
      if (done < r.n) return { status: "SWARM", done };
      if (el < s0 + swarmSecs + 1.8) return { status: "TALLY", done };
      return { status: "DONE", done };
    }
    return {
      async start(body) { await wait(250); return { run_id: build(body).id }; },
      async run(id) {
        const r = runs.get(id); if (!r) { const e = new Error("not found"); e.code = 404; throw e; }
        const st = state(r);
        return { run_id: id, status: st.status, n: r.n, done: st.done, failed: 0, plan: r.plan, parent_run_id: r.parent ? r.parent.id : null,
          calls: Math.ceil(st.done / 5), created_at: new Date(r.wall).toISOString(), finished_at: st.status === "DONE" ? new Date(r.wall + (performance.now() - r.t0)).toISOString() : "",
          grid: st.status === "GRID" ? null : r.grid, result: st.status === "DONE" ? r.result : null, labels: st.status === "DONE" ? r.labels : null };
      },
      async revise(id, body) {
        await wait(700); const r = runs.get(id);
        const sents = String(r.plan).split(/(?<=[.!?;])\s+/).filter(Boolean);
        return { bullets: [...sents.map((t) => ({ text: t, changed: false })), ...[...body.changes, ...body.checks].map((t) => ({ text: t.replace(/^\[\w+\]\s*/, ""), changed: true }))], conflicts: [] };
      },
      async reminders(id, body) { await wait(500); return { scheduled: body.reminders.map((r) => ({ at: r.at, text: r.text })) }; },
      async worries(id, limit = 30) {
        const r = runs.get(id); const st = state(r);
        const ws = r.worries.slice(0, st.done).slice(-limit).reverse();
        return { worries: ws.map((w) => ({ seq: "W#" + String(w.seq).padStart(4, "0"), character: w.character, group: w.group, angle: w.angle, moment: w.moment, failure: w.failure, severity: w.severity, early_sign: w.early_sign })) };
      },
    };
  }

  const params = new URLSearchParams(location.search);
  const MOCK = params.has("mock") ? Math.max(1, Math.min(1000, parseInt(params.get("mock"), 10) || 1000)) : 0;
  const API = MOCK ? makeMock(MOCK) : Real;
  if (MOCK) { const mb = document.createElement("div"); mb.className = "mock-badge"; mb.textContent = "Test mode · mock data · no Bedrock"; $("stage").appendChild(mb); document.querySelector(".honesty").textContent = "Test mode: every number on this page is mock data, generated in the browser. Worriers are angles on your plan, not people."; }

  /* ================= confetti (fixed fx canvas) ================= */
  const FX = (() => {
    const cv = $("fx"), ctx = cv.getContext("2d");
    let parts = [], raf = 0;
    function size() { const d = Math.min(2, devicePixelRatio || 1); cv.width = innerWidth * d; cv.height = innerHeight * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
    size(); addEventListener("resize", size);
    const cols = ["#FF3D71", "#2EE6A8", "#FACC15", "#8B5CF6", "#22D3EE", "#EC4899", "#F5F3FF"];
    function burst(x, y, n = 36, spread = 1) {
      if (reduce) return;
      for (let i = 0; i < n; i++) {
        const a = rand(-Math.PI * 0.95, -Math.PI * 0.05), v = rand(3, 8) * spread;
        parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, r: rand(0, 6), vr: rand(-0.3, 0.3), w: rand(5, 9), h: rand(3, 6), c: pick(cols), life: 1 });
      }
      if (!raf) loop();
    }
    function loop() {
      raf = requestAnimationFrame(() => {
        ctx.clearRect(0, 0, innerWidth, innerHeight);
        parts = parts.filter((p) => p.life > 0);
        for (const p of parts) {
          p.vy += 0.22; p.vx *= 0.99; p.x += p.vx; p.y += p.vy; p.r += p.vr; p.life -= 0.012;
          ctx.save(); ctx.globalAlpha = Math.max(0, Math.min(1, p.life * 1.6)); ctx.translate(p.x, p.y); ctx.rotate(p.r);
          ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore();
        }
        raf = 0; if (parts.length) loop(); else ctx.clearRect(0, 0, innerWidth, innerHeight);
      });
    }
    return { burst };
  })();

  /* ================= the stage: creatures on canvas ================= */
  const Stage = (() => {
    const stageEl = $("stage"), cv = $("cv"), ctx = cv.getContext("2d"), layer = $("layer");
    let W = 0, H = 0, dpr = 1, cs = [], seats = [], parts = [], size = 40, mode = "empty", huddles = [], back = [], benches = [], benchA = 1, backEnd = 0;
    const discs = { a: 1 };
    const groupIdx = new Map();
    const fps = { frames: 0, t0: performance.now(), value: 0 };

    function resize() {
      const r = cv.getBoundingClientRect();
      dpr = Math.min(2, devicePixelRatio || 1); W = r.width; H = r.height;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function genSeats(n, s, top) {
      const left = 18, right = W - 18, bottom = H - 12;
      const cx = W / 2, cy = bottom + s * 0.35, out = [];
      const rMin = Math.max(s * 1.9, Math.min(W, H) * 0.16);
      for (let k = 0; k < 400 && out.length < n; k++) {
        const r = rMin + k * s * 0.9;
        const per = Math.max(1, Math.floor((Math.PI * r) / (s * 0.98)));
        const sc = 1 - Math.min(0.22, k * 0.012);
        let got = 0;
        for (let j = 0; j < per; j++) {
          const th = (Math.PI * (j + 0.5)) / per;
          const x = cx - r * Math.cos(th), y = cy - r * Math.sin(th);
          if (x < left + s / 2 || x > right - s / 2 || y < top + s / 2 || y > bottom - s * 0.45) continue;
          out.push({ x, y, s: s * sc, row: k, th, r, cx, cy }); got++;
        }
        if (!got && r > Math.hypot(W / 2, H)) break;
      }
      return out;
    }

    function computeSeats(n, top = 64) {
      // largest creature size whose curved rows still seat everyone, so the crowd fills the stage
      let s = Math.min(118, Math.sqrt(((W - 36) * (H - top)) / Math.max(1, n)) * 1.6);
      let out = genSeats(n, s, top);
      while (out.length < n && s > 8) { s *= 0.965; out = genSeats(n, s, top); }
      size = s;
      out.sort((a, b) => a.row - b.row || a.th - b.th); // front rows first, left to right: a character's 5 moments sit together
      const used = out.slice(0, n);
      benches = [...new Map(used.map((st) => [st.row, { r: st.r, cx: st.cx, cy: st.cy, s: st.s }])).values()];
      return used;
    }

    function make(seq, seat) {
      return { seq, ci: -1, prop: "none", expr: "calm", x: seat.x, y: seat.y, s: seat.s, a: 0, pop: 0, shakeT: 0,
        ph: Math.random() * 6.28, blinkAt: performance.now() + rand(800, 6000), dim: 1, walk: 0, w: null };
    }

    function build(n) {
      resize(); mode = "seats"; huddles = []; back = []; clearLayer(); parts = [];
      seats = computeSeats(n);
      cs = seats.map((st, i) => make(i + 1, st));
    }

    function sleepers() {
      resize(); mode = "empty"; huddles = []; back = []; clearLayer(); parts = [];
      seats = computeSeats(46, 160);
      cs = seats.map((st, i) => Object.assign(make(i + 1, st), { ci: (i * 5) % C.PALETTE.length, prop: ["none", "hat", "phone", "apron", "glasses", "bow", "clipboard"][(i * 3) % 7], expr: "sleep", a: 1, dim: 0.55 }));
    }

    function costume(c, group) {
      if (!groupIdx.has(group)) groupIdx.set(group, groupIdx.size);
      const gi = groupIdx.get(group);
      c.ci = gi; c.prop = C.propFor(group, gi);
    }
    function setGroups(list) { groupIdx.clear(); list.forEach((g) => { if (!groupIdx.has(g)) groupIdx.set(g, groupIdx.size); }); }
    function clearLayer() { layer.querySelectorAll(".hlabel,.bubble").forEach((e) => e.remove()); }

    /* ----- huddles ----- */
    function huddleTargets(top, labels) {
      const slots = [[0.5, 0.66], [0.26, 0.45], [0.74, 0.45], [0.15, 0.8], [0.85, 0.8]];
      const members = top.map(() => []), leftovers = [];
      for (const c of cs) {
        const k = top.findIndex((t) => t.id === labels[String(c.seq)]);
        if (k >= 0) members[k].push(c); else leftovers.push(c);
      }
      const maxC = Math.max(1, ...members.map((m) => m.length));
      const R = Math.min(W * 0.16, H * 0.21);
      const step = Math.min(size * 0.62, (R / Math.sqrt(maxC)) * 1.02);
      const ts = Math.min(size * 1.05, step * 1.8);
      huddles = top.map((t, k) => {
        const cx = W * slots[k][0], cy = H * slots[k][1], ms = members[k];
        ms.forEach((c, i) => {
          const r = step * Math.sqrt(i + 0.6), a = i * 2.39996;
          c.tx = cx + Math.cos(a) * r; c.ty = cy + Math.sin(a) * r * 0.82; c.ts = ts; c.tdim = 1;
          c.ox = c.tx - cx; c.oy = c.ty - cy;
        });
        return { id: t.id, k, cx, cy, rad: step * Math.sqrt(ms.length + 0.6), members: ms, t, color: THEME_COLORS[k] };
      });
      const bs = Math.min(size, 16);
      const per = Math.max(1, Math.floor((W - 300) / (bs * 0.95)));
      back = leftovers;
      leftovers.forEach((c, i) => {
        const row = Math.floor(i / per), col = i % per;
        c.tx = 30 + bs / 2 + col * bs * 0.95 + (row % 2) * bs * 0.45; c.ty = 78 + row * bs * 0.8; c.ts = bs; c.tdim = 0.35;
      });
      backEnd = 30 + Math.min(leftovers.length, per) * bs * 0.95 + bs;
    }

    function gather(top, labels, total, instant = false) {
      huddleTargets(top, labels);
      mode = "huddles";
      if (instant) { cs.forEach((c) => { c.x = c.tx; c.y = c.ty; c.s = c.ts; c.dim = c.tdim; c.a = 1; }); labelsUp(total, true); return Promise.resolve(); }
      const tl = G.timeline();
      cs.forEach((c) => {
        const d = Math.hypot(c.tx - c.x, c.ty - c.y);
        tl.to(c, { x: c.tx, y: c.ty, s: c.ts, dim: c.tdim, duration: D(Math.min(1.8, 0.7 + d / 500)), ease: "power2.inOut",
          onStart() { c.walk = 1; }, onComplete() { c.walk = 0; } }, D(rand(0, 1.1)));
      });
      return new Promise((res) => tl.eventCallback("onComplete", () => { labelsUp(total); res(); }));
    }

    function labelsUp(total, instant) {
      layer.querySelectorAll(".hlabel").forEach((e) => e.remove());
      huddles.forEach((h, k) => {
        const el = document.createElement("div");
        el.className = "hlabel"; el.dataset.id = h.id;
        const official = typeof h.t.count === "number" ? h.t.count : h.members.length; // the tally's number, not the creature count
        el.innerHTML = `<b>${instant ? fmt(official) : 0}</b><span>similar worries grouped</span><em>${esc(h.t.cluster_name || h.t.name)}</em>`;
        el.style.setProperty("--c", THEME_COLORS[k]);
        el.style.left = `${Math.max(90, Math.min(W - 90, h.cx))}px`; el.style.top = `${h.cy - h.rad * 0.82 - 14}px`;
        layer.appendChild(el);
        if (instant) return;
        G.from(el, { scale: 0, rotation: rand(-12, 12), duration: D(0.55), delay: D(k * 0.12), ease: "back.out(2)" });
        const o = { v: 0 };
        G.to(o, { v: official, duration: D(1.3), delay: D(k * 0.12), ease: "power2.out", onUpdate() { el.querySelector("b").textContent = fmt(Math.round(o.v)); } });
      });
      if (back.length) {
        const el = document.createElement("div");
        el.className = "hlabel dimmed";
        el.innerHTML = `<b>${fmt(back.length)}</b><span>outside the top five</span>`;
        el.style.left = `${Math.min(W - 150, backEnd + 8)}px`; el.style.top = "64px";
        layer.appendChild(el);
        if (!instant) G.from(el, { opacity: 0, duration: D(0.6), delay: D(0.8) });
      }
    }

    function sigh(id, on) {
      const h = huddles.find((x) => x.id === id);
      if (!h) return;
      const f = on ? 0.84 : 1;
      h.members.forEach((c) => {
        G.to(c, { x: h.cx + c.ox * f, y: h.cy + c.oy * f, duration: D(0.9), ease: "sine.inOut" });
        G.fromTo(c, { pop: on ? -0.12 : 0.1 }, { pop: 0, duration: D(0.9), ease: "sine.out" });
      });
      const el = layer.querySelector(`.hlabel[data-id="${id}"]`);
      if (el) G.fromTo(el, { scale: 1.15 }, { scale: 1, duration: D(0.5), ease: "back.out(3)" });
    }

    function focus(id) {
      huddles.forEach((h) => {
        const on = !id || h.id === id;
        h.members.forEach((c) => G.to(c, { dim: on ? 1 : 0.28, duration: D(0.45) }));
        const el = layer.querySelector(`.hlabel[data-id="${h.id}"]`);
        if (el) el.classList.toggle("faded", !on);
      });
    }
    function huddleAt(px, py) {
      for (const h of huddles) {
        if (Math.hypot(px - h.cx, py - h.cy) < Math.max(36, h.rad * 1.1)) return h.id;
        if (Math.abs(px - h.cx) < 90 && py > h.cy - h.rad * 0.82 - 70 && py < h.cy - h.rad * 0.82) return h.id;   // its label
      }
      return null;
    }

    function home(dur = 1.2) {
      mode = "seats";
      layer.querySelectorAll(".hlabel").forEach((e) => G.to(e, { opacity: 0, scale: 0.6, duration: D(0.3), onComplete: () => e.remove() }));
      const tl = G.timeline();
      cs.forEach((c, i) => {
        const st = seats[i];
        tl.to(c, { x: st.x, y: st.y, s: st.s, dim: 1, duration: D(dur), ease: "power2.inOut", onStart() { c.walk = 1; }, onComplete() { c.walk = 0; } }, D(rand(0, 0.8)));
      });
      return new Promise((res) => tl.eventCallback("onComplete", res));
    }

    /* ----- per-creature reactions ----- */
    function react(c, sev) {
      c.expr = C.exprFor(sev);
      if (reduce) return;
      G.fromTo(c, { pop: 0.55 }, { pop: 0, duration: 0.6, ease: "elastic.out(1, .4)" });
      if (sev >= 4) c.shakeT = performance.now() + 1300;
    }
    function calmDown(c) {
      if (c.expr === "worried" || c.expr === "panic") parts.push({ k: "drop", x: c.x + c.s * 0.3, y: c.y - c.s * 0.25, vy: 0.4, life: 1, s: c.s });
      c.expr = "calm"; c.calmed = true; c.calmT = performance.now();
      if (!reduce) G.fromTo(c, { pop: 0.35 }, { pop: 0, duration: 0.7, ease: "elastic.out(1, .45)" });
    }

    /* ----- render loop ----- */
    let lastZ = 0;
    function frame(t) {
      fps.frames++; if (t - fps.t0 > 1000) { fps.value = Math.round((fps.frames * 1000) / (t - fps.t0)); fps.frames = 0; fps.t0 = t; }
      ctx.clearRect(0, 0, W, H);
      if (mode === "huddles") {
        // glowing floor disc under each huddle
        ctx.globalCompositeOperation = "lighter";
        for (const h of huddles) {
          const rx = h.rad * 1.45 + 26, ry = rx * 0.3, cy = h.cy + h.rad * 0.55;
          ctx.globalAlpha = discs.a * (reduce ? 0.6 : 0.55 + 0.1 * Math.sin(t / 700 + h.k));
          ctx.drawImage(C.glow(h.color), h.cx - rx, cy - ry, rx * 2, ry * 2);
          ctx.globalAlpha = discs.a * 0.5; ctx.strokeStyle = h.color; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.ellipse(h.cx, cy, rx * 0.78, ry * 0.78, 0, 0, 7); ctx.stroke();
        }
        ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
      }
      // amphitheatre benches under each row
      benchA += ((mode === "huddles" ? 0 : 1) - benchA) * 0.06;
      if (benchA > 0.02) {
        ctx.lineCap = "round";
        for (const b of benches) {
          ctx.globalAlpha = benchA;
          ctx.strokeStyle = "rgba(139,92,246,.16)"; ctx.lineWidth = Math.max(2, b.s * 0.12);
          ctx.beginPath(); ctx.arc(b.cx, b.cy + b.s * 0.3, b.r, Math.PI * 1.02, Math.PI * 1.98); ctx.stroke();
          ctx.strokeStyle = "rgba(34,211,238,.10)"; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(b.cx, b.cy + b.s * 0.42, b.r, Math.PI * 1.02, Math.PI * 1.98); ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }
      if (mode === "empty" && t - lastZ > 380 && cs.length && !reduce) {
        const c = pick(cs); parts.push({ k: "z", x: c.x + c.s * 0.22, y: c.y - c.s * 0.5, vy: -0.32, life: 1, s: c.s }); lastZ = t;
      }
      const order = cs.slice().sort((a, b) => a.y - b.y);
      // glow pass (additive): pink for worry, teal once calmed
      ctx.globalCompositeOperation = "lighter";
      const pinkG = C.glow("#FF3D71"), tealG = C.glow("#2EE6A8");
      for (const c of order) {
        if (c.a <= 0.01 || c.dim < 0.5) continue;
        const sz = c.s * (1 + c.pop), gy = c.y - sz * 0.12;
        const pulse = reduce ? 1 : 0.72 + 0.28 * Math.sin(t / 260 + c.ph);
        let pinkA = 0;
        if (c.expr === "panic") pinkA = (c.w && c.w.severity >= 5 ? 0.8 : 0.62) * pulse;
        else if (c.expr === "worried") pinkA = 0.4 * pulse;
        if (c.calmed) {
          const k = Math.min(1, (t - c.calmT) / 900);
          if (k < 1) { ctx.globalAlpha = c.a * 0.6 * (1 - k); ctx.drawImage(pinkG, c.x - sz, gy - sz, sz * 2, sz * 2); }
          ctx.globalAlpha = c.a * 0.42 * k * (reduce ? 1 : 0.8 + 0.2 * Math.sin(t / 500 + c.ph));
          ctx.drawImage(tealG, c.x - sz * 0.9, gy - sz * 0.9, sz * 1.8, sz * 1.8);
        } else if (pinkA) {
          const gs = sz * (c.expr === "panic" ? 1.05 : 0.9);
          ctx.globalAlpha = c.a * c.dim * pinkA; ctx.drawImage(pinkG, c.x - gs, gy - gs, gs * 2, gs * 2);
        }
      }
      ctx.globalCompositeOperation = "source-over";
      for (const c of order) {
        if (c.a <= 0.01) continue;
        if (t > c.blinkAt + 150) c.blinkAt = t + rand(2200, 7000);
        const blink = t > c.blinkAt && t < c.blinkAt + 150;
        const sz = c.s * (1 + c.pop);
        let x = c.x, y = c.y;
        if (!reduce) {
          y += Math.sin(t / 620 + c.ph) * c.s * 0.03;
          if (c.walk) y -= Math.abs(Math.sin(t / 85 + c.ph)) * c.s * 0.16;
          if (c.expr === "panic" && t < c.shakeT) x += Math.sin(t / 16 + c.ph) * c.s * 0.07;
        }
        ctx.globalAlpha = c.a * c.dim;
        ctx.drawImage(C.sprite(c.ci, c.prop, c.expr, blink), x - sz / 2, y - sz * 0.62, sz, sz);
        if (c.calmed && sz >= 16 && !reduce) {
          const tw = (Math.sin(t / 300 + c.ph * 3) + 1) / 2;
          if (tw > 0.55) {
            const sx = x + sz * 0.38, sy = y - sz * 0.55, r = sz * 0.09 * tw;
            ctx.globalAlpha = c.a * tw; ctx.fillStyle = "#D1FAE5";
            ctx.beginPath(); ctx.moveTo(sx, sy - r * 2); ctx.lineTo(sx + r * 0.5, sy - r * 0.5); ctx.lineTo(sx + r * 2, sy); ctx.lineTo(sx + r * 0.5, sy + r * 0.5);
            ctx.lineTo(sx, sy + r * 2); ctx.lineTo(sx - r * 0.5, sy + r * 0.5); ctx.lineTo(sx - r * 2, sy); ctx.lineTo(sx - r * 0.5, sy - r * 0.5); ctx.closePath(); ctx.fill();
          }
        }
        if (c.expr === "panic" && sz >= 22 && !reduce && c.dim > 0.5) {
          for (let k = 0; k < 2; k++) {
            const p = (t / 1000 + c.ph + k * 0.5) % 1;
            ctx.globalAlpha = c.a * 0.75 * (1 - p);
            ctx.fillStyle = "#FFD1DC";
            ctx.beginPath(); ctx.arc(x + (k ? 1 : -1) * sz * 0.3, y - sz * 0.62 - p * sz * 0.35, sz * 0.07 * (1 + p), 0, 7); ctx.fill();
          }
        }
      }
      ctx.globalAlpha = 1;
      parts = parts.filter((p) => p.life > 0);
      for (const p of parts) {
        p.y += p.vy; p.life -= p.k === "z" ? 0.008 : 0.02;
        if (p.k === "z") {
          ctx.globalAlpha = Math.min(0.9, p.life * 1.2); ctx.fillStyle = "#C4B5FD"; ctx.shadowColor = "#8B5CF6"; ctx.shadowBlur = 10;
          ctx.font = `800 ${Math.max(12, p.s * 0.3 * (1.4 - p.life * 0.4))}px "Plus Jakarta Sans", sans-serif`; ctx.fillText("z", p.x + Math.sin(p.life * 8) * 4, p.y);
          ctx.shadowBlur = 0;
        } else {
          p.vy += 0.25; ctx.globalAlpha = Math.max(0, p.life);
          ctx.fillStyle = "#7DD3FC";
          const r = Math.max(2.5, p.s * 0.07);
          ctx.beginPath(); ctx.moveTo(p.x, p.y - r * 1.6); ctx.quadraticCurveTo(p.x + r, p.y, p.x, p.y + r * 0.8); ctx.quadraticCurveTo(p.x - r, p.y, p.x, p.y - r * 1.6); ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
      requestAnimationFrame(frame);
    }

    function at(px, py) {
      let best = null, bd = Infinity;
      for (const c of cs) {
        if (c.a < 0.5) continue;
        const dx = px - c.x, dy = py - (c.y - c.s * 0.12), d = dx * dx + dy * dy;
        if (d < (c.s * 0.5) ** 2 && d < bd) { best = c; bd = d; }
      }
      return best;
    }

    function relayout() {
      const n = cs.length; if (!n) return;
      resize();
      seats = computeSeats(n, mode === "empty" ? 160 : 64);
      if (mode === "seats" || mode === "empty") cs.forEach((c, i) => { c.x = seats[i].x; c.y = seats[i].y; c.s = seats[i].s; });
    }

    resize();
    requestAnimationFrame(frame);
    let rt = 0; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(relayout, 150); });

    return {
      el: stageEl, layer, discs, build, sleepers, costume, setGroups, gather, sigh, focus, huddleAt, home, react, calmDown, at,
      seat: (i) => seats[i], creature: (seq) => cs[seq - 1],
      get cs() { return cs; }, get W() { return W; }, get H() { return H; }, get fps() { return fps.value; },
    };
  })();
  window.__theatre = { get fps() { return Stage.fps; }, get count() { return Stage.cs.length; } };

  /* ================= side widgets ================= */
  const Meter = (() => {
    const needle = $("needle"), st = { v: 0, live: false };
    function draw() { const j = st.live && !reduce ? rand(-2.2, 2.2) : 0; needle.style.transform = `rotate(${-90 + 180 * Math.max(0, Math.min(1, st.v)) + j}deg)`; }
    setInterval(() => { if (st.live) draw(); }, 90);
    draw();
    return {
      set(v, dur = 0.8) { G.to(st, { v, duration: D(dur), ease: "elastic.out(1, .55)", onUpdate: draw }); },
      live(on) { st.live = on; draw(); },
      text(done, n, sub) { $("m-done").textContent = fmt(done); $("m-n").textContent = fmt(n); if (sub !== undefined) $("m-sub").textContent = sub; },
      label(t) { $("m-what").textContent = t; },
    };
  })();

  const Ticker = (() => {
    const ol = $("ticker");
    function empty(m) { ol.innerHTML = `<li class="empty-t">${esc(m)}</li>`; }
    function add(c, w) {
      const fe = ol.querySelector(".empty-t"); if (fe) fe.remove();
      const li = document.createElement("li");
      li.style.setProperty("--sev", (w.severity || 3) >= 4 ? "#FF3D71" : (w.severity || 3) === 3 ? "#FB923C" : "#FACC15");
      const av = document.createElement("canvas"); av.width = av.height = 68;
      av.getContext("2d").drawImage(C.sprite(c.ci, c.prop, c.expr, false), 0, 0, 68, 68);
      li.appendChild(av);
      const d = document.createElement("div");
      d.innerHTML = `<div class="t-who">${esc(w.character)} · ${esc(w.moment)}</div><div class="t-what">${esc(w.failure)}</div>`;
      li.appendChild(d);
      ol.prepend(li);
      if (!reduce) G.from(li, { x: 30, opacity: 0, filter: "blur(8px)", duration: 0.45, ease: "power3.out", clearProps: "filter" });
      while (ol.children.length > 16) ol.lastChild.remove();
    }
    // throttle: at most one new card every 280 ms (always the newest worry), so each entrance is visible
    let pending = null, last = 0;
    function push(c, w) { pending = [c, w]; }
    setInterval(() => { if (pending && performance.now() - last > 270) { add(...pending); pending = null; last = performance.now(); } }, 70);
    return { add: push, empty: (m) => { pending = null; empty(m); } };
  })();

  const Stats = (() => {
    const st = { t0: 0, t1: 0, angles: new Map(), top: null };
    const fmtT = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
    setInterval(() => { if (st.t0) $("s-time").textContent = fmtT((st.t1 || Date.now()) - st.t0); }, 500);
    return {
      reset() { st.t0 = 0; st.t1 = 0; st.angles = new Map(); st.top = null; $("s-calls").textContent = "0"; $("s-landed").textContent = "0"; $("s-top").textContent = "–"; $("s-top-lbl").textContent = "loudest angle so far"; $("s-time").textContent = "0:00"; },
      times(start, end) { if (start) st.t0 = start; st.t1 = end || 0; if (st.t0) $("s-time").textContent = fmtT((st.t1 || Date.now()) - st.t0); },
      calls(n) { $("s-calls").textContent = fmt(n); },
      landed(n) { $("s-landed").textContent = fmt(n); },
      angle(a) {
        if (st.top) return;
        st.angles.set(a, (st.angles.get(a) || 0) + 1);
        let best = null, bn = 0; st.angles.forEach((v, k) => { if (v > bn) { best = k; bn = v; } });
        $("s-top").textContent = `${best} · ${fmt(bn)}`;
      },
      top(name, count) { st.top = name; $("s-top").textContent = `${name} · ${fmt(count)}`; $("s-top-lbl").textContent = "largest group"; },
    };
  })();

  const Bubbles = (() => {
    let rects = [];
    const BW = 200, BH = 74;
    function show(c, w) {
      if (rects.length >= 4) return false;
      const x = Math.max(BW / 2 + 52, Math.min(Stage.W - BW / 2 - 52, c.x));
      const bottom = Math.max(BH + 80, c.y - c.s * 0.72);
      const rect = { l: x - BW / 2 - 6, r: x + BW / 2 + 6, t: bottom - BH - 6, b: bottom + 6 };
      if (rects.some((o) => o.l < rect.r && rect.l < o.r && o.t < rect.b && rect.t < o.b)) return false;
      rects.push(rect);
      const b = document.createElement("div");
      b.className = "bubble" + (w.severity >= 4 ? " hot" : "");
      b.innerHTML = `<small>${esc(w.character)}</small>${esc(w.failure)}`;
      b.style.left = `${x}px`; b.style.top = `${bottom}px`;
      Stage.layer.appendChild(b);
      G.fromTo(b, { scale: 0.2, opacity: 0, transformOrigin: "50% 100%" }, { scale: 1, opacity: 1, duration: D(0.35), ease: "back.out(2.2)" });
      setTimeout(() => G.to(b, { scale: 0.6, opacity: 0, duration: D(0.25), onComplete: () => { b.remove(); rects = rects.filter((o) => o !== rect); } }), 2500);
      return true;
    }
    return { show, reset: () => { rects = []; } };
  })();

  const Status = (() => {
    const el = $("status"); let timer = 0;
    function set(text) {
      if (el.textContent === text) return;
      if (reduce) { el.textContent = text; return; }
      G.killTweensOf(el);
      G.to(el, { y: -12, opacity: 0, duration: 0.14, onComplete() { el.textContent = text; G.fromTo(el, { y: 12, opacity: 0 }, { y: 0, opacity: 1, duration: 0.25, ease: "back.out(2)" }); } });
    }
    function rotate(fn, ms = 2300) { stop(); set(fn()); timer = setInterval(() => set(fn()), ms); }
    function stop() { clearInterval(timer); }
    return { set, rotate, stop };
  })();

  /* ================= the show ================= */
  const S = { phase: "empty", run: null, parent: null, n: 100, queue: [], spoken: new Set(), castDone: false, casting: false, fullLoaded: false, pollT: 0, drainT: 0,
    dec: new Map(), items: [], revise: null, decSnap: null, open: null, pinned: false, replay: false, finishing: false, sevSum: 0, gridChars: [], token: 0 };
  const el = { plan: $("plan"), go: $("go"), compose: $("compose"), fixpanel: $("fixpanel"), retest: $("retest"), planMsg: $("plan-msg"), retestMsg: $("retest-msg"),
    empty: $("empty"), cards: $("cards"), spot: $("spot"), quiet: $("quiet"), results: $("results"), calm: $("calm"), hover: $("hover") };
  const msg = (node, t) => { node.textContent = t || ""; node.hidden = !t; };
  // one job per screen: compose (plan box), run (stage + live meter), review (findings + stage)
  function setPhase(ph) {
    if (document.body.dataset.ph === ph) return;
    document.body.dataset.ph = ph;
    dispatchEvent(new Event("resize"));
  }

  document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    el.plan.value = DEMOS[b.dataset.demo];
    document.querySelectorAll(".chip").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (!reduce) G.fromTo(el.plan, { scale: 0.98 }, { scale: 1, duration: 0.4, ease: "back.out(3)" });
  }));
  document.querySelectorAll(".toggle button").forEach((b) => b.addEventListener("click", () => {
    const all = [...document.querySelectorAll(".toggle button")];
    all.forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    document.querySelector(".toggle").style.setProperty("--i", all.indexOf(b));
    S.n = parseInt(b.dataset.n, 10); Sound.tick();
  }));
  // replays: show the crowd size the run actually used (or the smallest option it fits, if the grid capped it)
  function setCrowd(n) {
    const all = [...document.querySelectorAll(".toggle button")];
    if (!n || !all.length) return;
    const b = all.find((x) => +x.dataset.n === n) || all.find((x) => +x.dataset.n >= n) || all[all.length - 1];
    all.forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    document.querySelector(".toggle").style.setProperty("--i", all.indexOf(b));
    S.n = parseInt(b.dataset.n, 10);
  }
  $("sound").addEventListener("click", (e) => {
    Sound.on = !Sound.on; e.currentTarget.setAttribute("aria-pressed", String(Sound.on));
    if (Sound.on) { Sound.ensure(); Sound.chime(); }
  });

  el.go.addEventListener("click", async () => {
    const plan = el.plan.value.trim();
    if (plan.length < 10) { msg(el.planMsg, "Give them a bit more to worry about first."); G.fromTo(el.plan, { x: -6 }, { x: 0, duration: 0.4, ease: "elastic.out(1.2,.3)" }); return; }
    msg(el.planMsg, "");
    el.go.disabled = true;
    try {
      const { run_id } = await API.start({ plan, n: S.n });
      begin(run_id, { plan, n: S.n });
    } catch (e) { msg(el.planMsg, e.message); } finally { el.go.disabled = false; }
  });

  function resetUI() {
    S.token++;
    clearTimeout(S.pollT); clearInterval(S.drainT); Status.stop();
    S.queue = []; S.spoken = new Set(); S.castDone = false; S.casting = false; S.fullLoaded = false; S.finishing = false; S.sevSum = 0; S.replay = false; S.queuedShown = false;
    G.killTweensOf(el.cards.children);
    el.cards.innerHTML = ""; el.spot.hidden = true; el.quiet.hidden = true; el.results.hidden = true; el.calm.hidden = true; el.hover.hidden = true;
    Stage.el.querySelectorAll(".stamp").forEach((s) => s.remove());
    Bubbles.reset();
  }

  function idle() {
    resetUI();
    S.phase = "empty"; S.run = null; S.parent = null; S.dec = new Map(); S.items = []; S.revise = null; S.decSnap = null; S.pinned = false;
    setPhase("compose");
    el.compose.hidden = false; el.fixpanel.hidden = true; el.empty.hidden = false;
    Stage.sleepers(); Status.set("Ready when you are"); Stats.reset();
    document.querySelector(".status-chip").hidden = true;
    Meter.set(0); Meter.live(false); Meter.text(0, 0, "Waiting for a plan.");
    Ticker.empty("Worries slide in here as each worrier speaks.");
    history.replaceState(null, "", location.pathname + location.search);
  }
  $("new-plan").addEventListener("click", idle);

  /* ----- phase 1: casting ----- */
  const castLines = (chars) => () => pick([
    "Hiring worriers…", "Checking everyone's worry licence…", "Handing out tiny hats…", "Warming up the gasps…",
    ...(chars.length ? [`Interviewing ${pick(chars).name}…`, `${pick(chars).name} has entered the chat`, `Teaching ${pick(chars).name} to fret properly…`] : []),
  ]);

  function deck() {
    el.cards.innerHTML = "";
    const cx = Stage.W / 2 - 43, cy = Stage.H / 2 - 52;
    for (let i = 0; i < 5; i++) {
      const d = document.createElement("div"); d.className = "ccard back"; el.cards.appendChild(d);
      G.set(d, { x: cx + i * 3, y: cy - i * 2, rotation: rand(-10, 10) });
      if (!reduce) G.to(d, { y: cy - 16 - i * 2, rotation: rand(-16, 16), duration: 0.45, repeat: -1, yoyo: true, delay: i * 0.09, ease: "sine.inOut" });
    }
  }

  async function castGrid(grid, token) {
    S.casting = true;
    const chars = grid.characters || [];
    S.gridChars = chars;
    const groupsInOrder = [...new Set(chars.map((c) => c.group))];
    Stage.setGroups(groupsInOrder);
    Status.rotate(castLines(chars), 1900);
    G.killTweensOf(el.cards.children);
    el.cards.innerHTML = "";
    const n = Math.min(25, chars.length), cols = n > 16 ? 5 : 4, cw = 86, ch = 104, gap = 10;
    const rows = Math.ceil(n / cols);
    const gw = cols * cw + (cols - 1) * gap, gh = rows * ch + (rows - 1) * gap;
    const scale = Math.min(1, (Stage.H - 110) / gh, (Stage.W - 140) / gw);
    const ox = Stage.W / 2 - (gw * scale) / 2, oy = 80 + (Stage.H - 80 - gh * scale) / 2;
    const cards = chars.slice(0, n).map((c, i) => {
      const d = document.createElement("div"); d.className = "ccard";
      const cv = document.createElement("canvas"); cv.width = cv.height = 108;
      const gi = groupsInOrder.indexOf(c.group);
      cv.getContext("2d").drawImage(C.sprite(gi, C.propFor(c.group, gi), "calm", false), 0, 0, 108, 108);
      d.appendChild(cv);
      const p = document.createElement("p"); p.textContent = c.name; d.appendChild(p);
      d.title = c.group;
      el.cards.appendChild(d);
      const col = i % cols, row = Math.floor(i / cols);
      return { d, x: ox + col * (cw + gap) * scale, y: oy + row * (ch + gap) * scale };
    });
    await new Promise((res) => {
      const tl = G.timeline({ onComplete: res });
      cards.forEach((c, i) => tl.fromTo(c.d, { x: Stage.W / 2 - 43, y: Stage.H + 30, rotation: rand(-40, 40), scale: scale * 0.6, transformOrigin: "0 0" },
        { x: c.x, y: c.y, rotation: rand(-5, 5), scale, duration: D(0.5), ease: "back.out(1.5)", onStart: () => Sound.pop() }, D(i * 0.07)));
    });
    if (token !== S.token) return;
    await wait(1500);
    if (token !== S.token) return;
    await new Promise((res) => {
      const tl = G.timeline({ onComplete: res });
      cards.forEach((c, i) => {
        const st = Stage.seat(Math.floor((i / n) * Stage.cs.length)) || { x: Stage.W / 2, y: Stage.H / 2 };
        tl.to(c.d, { x: st.x - 9, y: st.y - 10, scale: 0.2, opacity: 0, rotation: rand(-90, 90), duration: D(0.6), ease: "power2.in" }, D(i * 0.03));
      });
      tl.to(Stage.cs, { a: 1, duration: D(0.25), stagger: { amount: D(Math.min(1.6, 0.4 + Stage.cs.length / 600)), from: "center" } }, D(0.35));
    });
    el.cards.innerHTML = "";
    S.casting = false; S.castDone = true;
  }

  /* ----- phase 2: panic ----- */
  function startDrain() {
    clearInterval(S.drainT);
    S.drainT = setInterval(() => {
      if (!S.castDone || !S.queue.length) { maybeFinish(); return; }
      const per = S.replay ? Math.max(1, Math.ceil(S.queue.length / 45)) : Math.max(1, Math.ceil(S.queue.length / 12));
      for (let i = 0; i < per && S.queue.length; i++) speak(S.queue.shift());
      updateMeter();
    }, 130);
  }

  function speak(w) {
    const seq = seqOf(w);
    if (S.spoken.has(seq)) return;
    const c = Stage.creature(seq);
    if (!c) return;
    S.spoken.add(seq); S.sevSum += w.severity || 3;
    Stats.landed(S.spoken.size); if (w.angle) Stats.angle(w.angle);
    c.w = w;
    if (c.ci < 0 && w.group) Stage.costume(c, w.group);
    Stage.react(c, w.severity || 3);
    if (S.run.n <= 100 || Math.random() < (S.replay ? 0.06 : 0.12)) Bubbles.show(c, w);
    if (S.run.n <= 200 || Math.random() < 0.25) Ticker.add(c, w);
    if ((w.severity || 0) >= 5 && Math.random() < 0.35) Sound.gasp(); else Sound.pop();
  }

  function updateMeter() {
    const r = S.run; if (!r) return;
    const spoken = S.spoken.size, avg = spoken ? S.sevSum / spoken : 0;
    Meter.set((spoken / (r.n || 1)) * (avg / 5), 0.6);
    Meter.label(S.run && S.run.isRetest ? "have looked again" : "have spoken");
    Meter.text(spoken, r.n, spoken ? `Average worry so far: ${avg.toFixed(1)} out of 5` : "Waiting for the first worry…");
  }

  const panicLines = () => {
    const r = S.run, c = S.gridChars.length ? pick(S.gridChars).name : "Someone";
    return pick([`${fmt(S.spoken.size)} of ${fmt(r.n)} have spoken`, `${c} is spiralling`, `${c} needs a moment`, `${fmt(S.spoken.size)} of ${fmt(r.n)} have spoken`, "Panic level: rising", `${c} would like to raise a concern`]);
  };

  /* ----- polling ----- */
  function begin(id, opts = {}) {
    resetUI();
    const token = S.token;
    S.phase = "casting";
    S.replay = !!opts.replay;
    S.run = { id, n: opts.n || S.n, status: "STARTING", result: null, labels: null, plan: opts.plan || "" };
    history.replaceState(null, "", `${location.pathname}${location.search}#run=${id}`);
    el.empty.hidden = true; el.compose.hidden = false; el.fixpanel.hidden = true;
    S.dec = new Map(); S.revise = null; S.decSnap = null;
    setPhase("run");
    document.querySelector(".status-chip").hidden = false;
    Stage.build(S.run.n);
    Ticker.empty("Listening for the first worry…");
    Stats.reset(); Stats.times(Date.now(), 0);
    Meter.live(true); Meter.set(0.02); Meter.text(0, S.run.n, "Casting the panel…");
    deck(); Status.rotate(castLines([]), 1900);
    startDrain();
    poll(token);
  }

  const QUEUED_LINE = "Another plan is being worried about. Yours is next in line…";
  async function poll(token) {
    if (token !== S.token) return;
    const r = S.run;
    try {
      const meta = await API.run(r.id);
      if (token !== S.token) return;
      if (meta.n && meta.n !== r.n && !S.castDone && !S.casting && !r.isRetest) { r.n = meta.n; Stage.build(r.n); }
      r.status = meta.status; r.n = meta.n || r.n; r.plan = meta.plan || r.plan; r.done = meta.done;
      if (typeof meta.calls === "number") Stats.calls(meta.calls);
      Stats.times(meta.created_at ? Date.parse(meta.created_at) : 0, meta.status === "DONE" && meta.finished_at ? Date.parse(meta.finished_at) : 0);
      if (meta.status === "FAILED") { Status.stop(); Status.set("This run fell over. Please try again."); Meter.live(false); el.cards.innerHTML = ""; return; }
      // F05: one swarm runs at a time; a queued run waits, and gives up cleanly (BUSY) after the queue limit.
      if (meta.status === "BUSY") { Status.stop(); Status.set("The worriers are busy with other plans. Nothing was used. Please try again in a few minutes."); Meter.live(false); el.cards.innerHTML = ""; return; }
      if (meta.status === "QUEUED" && !S.queuedShown && !S.casting) { S.queuedShown = true; Status.stop(); Status.set(QUEUED_LINE); }
      if (meta.grid && !S.castDone && !S.casting && !r.isRetest) castGrid(meta.grid, token).then(() => { if (token === S.token && !S.finishing && S.run.status === "QUEUED") { S.queuedShown = true; Status.set(QUEUED_LINE); return; } if (token === S.token && !S.finishing) { Status.set("Places, everyone. Begin worrying!"); setTimeout(() => { if (token === S.token && !S.finishing && S.phase === "casting") Status.rotate(panicLines, 2100); }, 1600); } });
      if (S.queuedShown && meta.status === "SWARM") { S.queuedShown = false; if (S.castDone && !r.isRetest) Status.rotate(panicLines, 2100); }
      if (["SWARM", "TALLY", "DONE"].includes(meta.status)) {
        if (meta.status === "DONE" && !S.fullLoaded) {
          const { worries } = await API.worries(r.id, 1000);
          if (token !== S.token) return;
          enqueue(worries); S.fullLoaded = true;
          r.result = meta.result; r.labels = meta.labels || {};
        } else if (meta.status !== "DONE" && r.done > S.spoken.size + S.queue.length) {
          const { worries } = await API.worries(r.id, 30);
          if (token !== S.token) return;
          enqueue(worries);
        }
      }
      if (meta.status === "TALLY" && S.castDone && !S.queue.length) Status.rotate(() => pick(["Counting who agrees with whom…", "Sorting worries into piles…", "Tallying. In code, not by vibes."]), 2000);
    } catch (e) {
      if (e.code !== 404) { Status.set(e.message); if (/limit/.test(e.message)) return; }
    }
    if (S.run && S.run.status !== "DONE" && token === S.token) S.pollT = setTimeout(() => poll(token), 1500);
  }

  function enqueue(ws) {
    const have = new Set(S.queue.map(seqOf));
    ws.forEach((w) => { const s = seqOf(w); if (!S.spoken.has(s) && !have.has(s)) { S.queue.push(w); have.add(s); } });
    S.queue.sort((a, b) => seqOf(a) - seqOf(b));
  }

  async function maybeFinish() {
    const r = S.run;
    if (!r || r.status !== "DONE" || !S.fullLoaded || S.queue.length || !S.castDone || S.finishing) return;
    S.finishing = true;
    clearInterval(S.drainT);
    const token = S.token;
    updateMeter();
    await wait(900);
    if (token !== S.token) return;
    if (r.isRetest) retestFinale(token); else gathering(token);
  }

  /* ----- phase 3: the gathering ----- */
  async function gathering(token) {
    const r = S.run, res = r.result;
    S.phase = "gathering"; Status.stop(); Meter.live(false);
    Status.set("The Gathering: everyone, find your people!");
    const top = res.top5;
    const inTop = top.reduce((a, t) => a + t.count, 0);
    Meter.set(inTop / res.n_worries, 1.4);
    Meter.label("in the top five"); Meter.text(inTop, res.n_worries, "share of worriers in the top five huddles");
    Stats.top(top[0].cluster_name || top[0].name, top[0].count);
    setPhase("review"); el.compose.hidden = true; el.fixpanel.hidden = false;
    S.items = buildItems(res); renderList(); $("rv-detail").hidden = true; $("rv-list").hidden = false;
    await wait(260);   // let the stage re-measure before the huddles are placed
    if (token !== S.token) return;
    await Stage.gather(top, r.labels || {}, res.n_worries);
    if (token !== S.token) return;
    Status.set(`Largest group of similar worries: ${top[0].cluster_name || top[0].name}`);
    await wait(2800);
    if (token !== S.token) return;
    if (res.affected || res.quiet) quietWorry(token); else fixes();
  }
  // "what she actually wants" for a proposal, the generic angle wording otherwise
  function wantPhrase(plan) {
    return /\bpropos(e|al|ing)\b/i.test(String(plan || (S.parent && S.parent.plan) || "")) ? "what she actually wants" : "what the people affected actually want";
  }

  /* ----- phase 4: the spotlight: what the people affected want (+ the quiet worry as a smaller line) ----- */
  async function quietWorry(token) {
    const res = S.run.result, a = res.affected, q = res.quiet;
    S.spotSeq = a ? a.seq : q.seq;
    const c = S.spotSeq != null ? Stage.creature(S.spotSeq) : null;
    S.phase = "quiet";
    Status.set("Shh. One more thing…");
    Stage.layer.querySelectorAll(".hlabel").forEach((e) => G.to(e, { opacity: 0, duration: D(0.4) }));
    G.to(Stage.cs.filter((x) => x !== c), { dim: 0.12, duration: D(1.2) });
    G.to(Stage.discs, { a: 0.08, duration: D(1.2) });
    const st = { x: Stage.W / 2, y: -80, r: 300 };
    const apply = () => { el.spot.style.setProperty("--x", `${st.x}px`); el.spot.style.setProperty("--y", `${st.y}px`); el.spot.style.setProperty("--r", `${st.r}px`); };
    apply(); el.spot.hidden = false;
    G.fromTo(el.spot, { opacity: 0 }, { opacity: 1, duration: D(0.8) });
    const tx = c ? c.x : Stage.W / 2, ty = c ? c.y - c.s * 0.2 : Stage.H / 2;
    await new Promise((resv) => G.to(st, { x: tx, y: ty, r: Math.max(40, (c ? c.s : 30) * 1.25), duration: D(1.7), ease: "power3.inOut", onUpdate: apply, onComplete: resv }));
    if (token !== S.token) return;
    Sound.gasp();
    if (c) {
      c.dim = 1; c.home = { x: c.x, y: c.y, s: c.s };
      await new Promise((r) => G.to(c, { pop: 0.5, duration: D(0.35), yoyo: true, repeat: 1, ease: "sine.inOut", onComplete: r }));
      const fx = Stage.W / 2, fy = Stage.H * 0.86, fs = Math.min(110, Stage.H * 0.14);
      c.walk = 1;
      await new Promise((r) => G.timeline({ onComplete: r })
        .to(c, { x: fx, y: fy, s: fs, duration: D(1.6), ease: "power2.inOut", onComplete() { c.walk = 0; } }, 0)
        .to(st, { x: fx, y: fy - fs * 0.2, r: fs * 0.95, duration: D(1.6), ease: "power2.inOut", onUpdate: apply }, 0));
    }
    if (token !== S.token) return;
    el.quiet.style.top = "96px";
    el.quiet.hidden = false;
    let full;
    const also = $("q-also");
    also.hidden = true;
    if (a) {
      const wp = wantPhrase(S.run.plan);
      $("q-eyebrow").textContent = wp === "what she actually wants" ? "What she actually wants" : "What the people affected want";
      const sp = a.spotlight || [];
      if (sp.length) {
        // spotlight cards: possibilities to ask about, with no agreement count (counts were unstable on recount)
        const c0 = sp[0];
        full = `${cap(c0.person)} might want ${String(c0.need).replace(/\.$/, "")}.`;
        $("q-worry").innerHTML = `<span class="q-line"><b>Your plan says</b> “${esc(c0.plan_quote)}”</span>` +
          (c0.unknown ? `<span class="q-line"><b>Not known yet</b> ${esc(c0.unknown)}</span>` : "") +
          `<span class="q-line"><b>Question to ask</b> ${esc(c0.question)}</span>` +
          `<span class="q-line q-small">One worrier wrote: “${esc(c0.worry)}” Worriers are angles, not people.</span>`;
        if (sp.length > 1) {
          also.innerHTML = `<b>Also worth asking:</b> ${sp.slice(1).map((c) => esc(c.question)).join(" · ")}`;
          also.hidden = false;
        }
      } else {
        full = `Of ${fmt(a.of)} worriers asked ${wp}, ${fmt(a.count)} said: “${String(a.worry).trim()}”`;
        $("q-worry").textContent = a.character ? `One of them: ${a.character}, at “${a.moment}”. Worriers are angles, not people.` : "Worriers are angles, not people.";
      }
      if (!sp.length && q && q.worry && q.worry !== a.worry) {
        also.innerHTML = `<b>And a quieter one:</b> “${esc(q.question || q.worry)}”`;
        also.hidden = false;
      }
    } else {
      $("q-eyebrow").textContent = "The quiet worry";
      const q1 = String(q.question || q.worry).trim().replace(/^./, (m) => m.toLowerCase());
      full = `One worrier, far from the main groups, asked: ${q1}`;
      $("q-worry").textContent = `“${q.worry}” — ${q.character}, at “${q.moment}”`;
    }
    const tEl = $("q-text"); tEl.innerHTML = '<span class="caret"></span>';
    G.set([".q-worry", ".q-also", ".q-actions"], { opacity: 0 });
    for (let i = 1; i <= full.length; i++) {
      if (token !== S.token) return;
      tEl.innerHTML = `${esc(full.slice(0, i))}<span class="caret"></span>`;
      if (!reduce) await new Promise((r) => setTimeout(r, full[i - 1] === ":" ? 420 : 34));
    }
    G.to(".q-worry", { opacity: 1, duration: D(0.4) });
    G.to(".q-also", { opacity: 1, duration: D(0.4), delay: D(0.15) });
    G.to(".q-actions", { opacity: 1, duration: D(0.4), delay: D(0.3) });
  }
  function endQuiet() {
    const c = S.spotSeq != null ? Stage.creature(S.spotSeq) : null;
    if (c && c.home) { c.walk = 1; G.to(c, { x: c.home.x, y: c.home.y, s: c.home.s, pop: 0, duration: D(1.1), ease: "power2.inOut", onComplete() { c.walk = 0; } }); }
    Stage.cs.forEach((x) => { if (x !== c) G.to(x, { dim: x.tdim ?? 1, duration: D(0.9) }); });
    G.to(Stage.discs, { a: 1, duration: D(0.9) });
    Stage.layer.querySelectorAll(".hlabel").forEach((e) => G.to(e, { opacity: 1, duration: D(0.5) }));
    G.to(el.spot, { opacity: 0, duration: D(0.6), onComplete: () => { el.spot.hidden = true; } });
    el.quiet.hidden = true;
  }
  $("q-matters").addEventListener("click", (e) => {
    S.pinned = true; Sound.chime();
    const r = e.currentTarget.getBoundingClientRect(); FX.burst(r.left + r.width / 2, r.top, 46);
    endQuiet(); fixes(S.run.result.affected ? "affected" : "quiet");
  });
  $("q-skip").addEventListener("click", () => { endQuiet(); fixes(); });

  /* ----- phase 5: review. Each finding: its evidence in the plan, what the plan does not say, a suggested fix, and the
     planner's decision (already handled / needs this fix / need to find out). Decisions stay in three separate layers:
     accepted changes and planned checks go into the plan; "already handled" is the planner's word and is never evidence. ----- */
  const ST = {
    supported: ["Grounded in your plan", "st-sup"],
    unverified: ["Your plan doesn't say", "st-unv"],
    contradicted: ["Your plan covers this", "st-cov"],
  };
  const DEC = { handled: "Reported handled by you · not verified", fix: "Fix accepted (planned, not done)", find: "To find out" };
  const basePlan = (p) => String(p || "").split(/\n\nChanges made to the plan:/)[0];

  function buildItems(res) {
    const wp = wantPhrase(S.run && S.run.plan);
    const items = res.top5.map((t, i) => ({ key: t.id, kind: "top", color: THEME_COLORS[i], name: t.name, count: t.count, of: res.n_worries, card: t.card,
      clusterCount: typeof t.cluster_count === "number" ? t.cluster_count : t.count, clusterName: t.cluster_name || "", examples: t.examples,
      grounding: t.grounding, fix: t.fix, owner: t.owner, moment: t.fix_moment, when: t.when, how: t.how_to_check, ifFails: t.if_check_fails, backup: t.backup,
      sign: t.early_sign, days: t.days_before, risk: t.introduces_risk === true, riskNote: t.risk_note, review: t.fix_review,
      partial: t.partial === true, stillOpen: t.still_open, openQ: t.open_question === true, needsReview: t.needs_review === true, validation: t.validation,
      fixUnchecked: t.fix_unchecked === true }));
    const a = res.affected, q = res.quiet;
    if (a) items.unshift({ key: "affected", kind: "affected", color: "#F472B6", name: wp === "what she actually wants" ? "What she actually wants" : "What the people affected want",
      count: a.count, of: a.of, card: a.card, grounding: a.grounding, seq: a.seq, worry: a.worry, others: a.other_concerns, spotlight: a.spotlight, review: a.fix_review, fix: a.fix, owner: a.owner, moment: a.fix_moment, when: a.when,
      how: a.how_to_check, ifFails: a.if_check_fails, backup: a.backup, sign: a.early_sign, days: a.days_before, risk: a.introduces_risk === true, riskNote: a.risk_note,
      partial: a.partial === true, stillOpen: a.still_open, fixUnchecked: a.fix_unchecked === true });
    if (q) items.push({ key: "quiet", kind: "quiet", color: "#8B5CF6", name: "The quiet worry", count: q.count, of: res.n_worries, card: q.card, grounding: q.grounding,
      seq: q.seq, worry: q.worry, review: q.fix_review, fix: q.fix, owner: q.owner, when: q.when, how: q.how_to_check, ifFails: q.if_check_fails, backup: q.backup, sign: q.early_sign,
      days: q.days_before, risk: q.introduces_risk === true, riskNote: q.risk_note, partial: q.partial === true, stillOpen: q.still_open, openQ: q.open_question === true,
      needsReview: q.needs_review === true, validation: q.validation, fixUnchecked: q.fix_unchecked === true });
    return items;
  }

  function fixes(openKey) {
    const res = S.run.result;
    S.phase = "fixes";
    setPhase("review");
    Status.set("Open a finding. Decide what to do about it.");
    el.compose.hidden = true; el.fixpanel.hidden = false; msg(el.retestMsg, "");
    S.items = buildItems(res);
    const fu = res.funnel, g = res.grounding;
    const gc = res.goal_context;
    $("rv-sub").innerHTML = `Click a finding or a huddle. For each one, you decide: already handled, needs the fix, or find out first.` +
      (gc && gc.goal ? `<span class="funnel">Goal the fixes keep in mind (read from your plan, not stated by you): ${esc(gc.goal)}${(gc.preserve || []).length ? ` · keep: ${gc.preserve.map(esc).join("; ")}` : ""}</span>` : "") +
      ((res.set_aside || []).length ? `<span class="funnel">Set aside, not backed by your plan: ${res.set_aside.map((x) => `${esc(x.name)}${x.needs ? ` (needs ${esc(x.needs)}, which your plan never mentions)` : ""}`).join("; ")}</span>` : "") +
      (fu && typeof fu.generated === "number" ? `<span class="funnel">${fmt(fu.generated)} imagined → ${fmt(fu.valid)} valid → ${fmt(fu.specific)} specific → ${fmt(fu.plausible)} counted (near-duplicates share one vote)${(fu.judged_out_clusters || []).length ? ` · ${fu.judged_out_clusters.length} group${fu.judged_out_clusters.length > 1 ? "s" : ""} judged implausible` : ""}${g ? ` · ${fmt(g.quoted)} quoted your plan, ${fmt(g.omission)} named something it leaves out, ${fmt(g.ungrounded)} gave a quote that is not in it` : ""}</span>` : "");
    renderList();
    if (openKey && S.items.some((x) => x.key === openKey)) detail(openKey); else closeDetail();
  }

  // one status tag per finding: review / open question / not fully checked / the card's own assessment
  function tagOf(f, st) {
    if (f.needsReview) return `<i class="tg st-ask">Needs your review</i>`;
    if (f.openQ) return `<i class="tg st-ask">Open question</i>`;
    if (f.validation === "unavailable") return `<i class="tg">Not fully checked</i>`;
    return st ? `<i class="tg ${st[1]}">${st[0]}</i>` : "";
  }
  function renderList() {
    $("rv-list").innerHTML = S.items.map((f) => {
      const st = f.card ? ST[f.card.assessment] : null, d = S.dec.get(f.key);
      return `<button type="button" class="rv-item${d ? " decided" : ""}" data-k="${esc(f.key)}" style="--c:${f.color}">
        <span class="rv-dot">${f.kind === "affected" ? "💗" : f.kind === "quiet" ? "🔦" : ""}</span>
        <span class="rv-nm">${esc(f.name)}</span><span class="rv-ct">${f.spotlight && f.spotlight.length ? `<small>${f.spotlight.length} to ask</small>` : ""}</span>
        <span class="rv-tags">${tagOf(f, st)}${d ? `<i class="tg dec">${esc(DEC[d])}</i>` : ""}</span></button>`;
    }).join("");
    $("rv-list").querySelectorAll(".rv-item").forEach((b) => b.addEventListener("click", () => detail(b.dataset.k)));
    syncRetest();
  }

  function marked(plan, quotes) {
    // quotes are exact spans of the plan (checked in code by the tally); mark each occurrence
    const spans = [];
    quotes.forEach(([q, cls]) => { const i = q ? plan.indexOf(q) : -1; if (i >= 0) spans.push([i, i + q.length, cls]); });
    spans.sort((x, y) => x[0] - y[0]);
    let out = "", at = 0;
    spans.forEach(([a, b, cls]) => { if (a < at) return; out += esc(plan.slice(at, a)) + `<mark class="${cls}">${esc(plan.slice(a, b))}</mark>`; at = b; });
    return out + esc(plan.slice(at));
  }

  function detail(key) {
    const f = S.items.find((x) => x.key === key); if (!f) return;
    S.open = key;
    const cd = f.card || {}, st = ST[cd.assessment], d = S.dec.get(key);
    const covered = cd.assessment === "contradicted";
    const plan = basePlan(S.run.plan);
    const gr = f.grounding, sp = !!(f.spotlight && f.spotlight.length);
    const box = $("rv-detail");
    box.innerHTML = `
      <button type="button" class="linkish rv-back">← All findings</button>
      <div class="rv-h" style="--c:${f.color}"><span class="rv-dot">${f.kind === "affected" ? "💗" : f.kind === "quiet" ? "🔦" : ""}</span><h3>${esc(f.name)}</h3></div>
      ${f.spotlight && f.spotlight.length ? `<p class="rv-meta">Possibilities to ask about, from the worriers who looked at ${esc(wantPhrase(S.run && S.run.plan))}. No count: these are questions, not findings.</p>
      <div class="rv-spot">${f.spotlight.map((c) => `<div class="rv-spotcard"><p class="rv-fail">${esc(cap(c.person))} might want ${esc(String(c.need).replace(/\.$/, ""))}.</p>
        <p class="rv-line"><b>Your plan says</b>“${esc(c.plan_quote)}”</p>${c.unknown ? `<p class="rv-line"><b>Not known yet</b>${esc(c.unknown)}</p>` : ""}
        <p class="rv-q"><b>Question to ask</b>${esc(c.question)}</p><p class="rv-ground">One worrier wrote: “${esc(c.worry)}”</p></div>`).join("")}</div>`
      : `<p class="rv-meta">Raised in the pre-mortem · ${tagOf(f, st) || '<i class="tg">Not checked</i>'}</p>
      ${f.worry ? `<p class="rv-worry">“${esc(f.worry)}”</p>` : ""}`}
      ${f.kind === "affected" && f.others && f.others.length > 1 ? `<p class="rv-ground">Other concerns from the same worriers: ${f.others.slice(1, 4).map((g) => `${esc(g.concern)} (${fmt(g.count)})`).join("; ")}</p>` : ""}
      ${f.needsReview ? `<p class="rv-review"><b>Needs your review</b>The summary written for this group did not match most of its worries, so it is not shown and no fix is suggested. What worriers in this group raised:</p>
        <ul class="rv-raised">${(f.examples || []).slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${cd.failure && !sp && !f.needsReview ? `<p class="rv-fail">${esc(cd.failure)}</p>` : ""}
      ${cd.represents === "mixed" && !f.needsReview ? `<p class="rv-ground">This finding covers one part of a group of similar worries; the group also holds other concerns (see the text grouping below).</p>` : ""}
      ${cd.mechanism && !sp && !f.openQ && !f.needsReview ? `<p class="rv-line"><b>How it fails</b>${esc(cd.mechanism)}</p>` : ""}
      ${sp || f.needsReview ? "" : `<div class="rv-plan"><b>Your plan${(cd.evidence || []).length ? ", with the words this risk depends on" : ""}</b><p>${marked(plan, [...(cd.evidence || []).map((q) => [q, "ev"]), [cd.control_quote, "ctl"]])}</p></div>`}
      ${(cd.assumptions || []).length && !sp && !f.needsReview ? `<p class="rv-line"><b>Your plan doesn't say</b>${cd.assumptions.map(esc).join("; ")}</p>` : ""}
      ${covered ? `<p class="rv-covered"><b>Your plan already covers this</b>“${esc(cd.control_quote)}”: ${esc(cd.contradiction_reason)}</p>` : ""}
      ${cd.question ? `<p class="rv-q"><b>Find out</b>${esc(cd.question)}</p>` : ""}
      ${f.openQ ? `<p class="rv-review"><b>Open question</b>This depends on something your plan doesn't say${cd.needs ? ` (${esc(cd.needs)})` : ""}, so no fix is suggested. Find out first.</p>` : ""}
      ${!covered && !f.fix && f.review && !f.openQ && !f.needsReview ? `<p class="rv-review"><b>No reliable fix</b>The suggested fixes did not stop this failure, so none is shown. This one needs your own judgement.</p>` : ""}
      ${!covered && f.fix ? `<div class="rv-fix"><b>Suggested fix${f.partial ? " (partial)" : ""}</b><p>${esc(cleanFix(f.fix))}</p>
        ${f.partial ? `<p class="fix-risk">Partial: this reduces the risk but does not close it.${f.stillOpen ? ` Still open: ${esc(f.stillOpen)}` : ""}</p>` : ""}
        ${f.fixUnchecked ? `<p class="fix-risk">Not fully checked: a quality check on this fix did not run. Judge it yourself.</p>` : ""}
        <dl>${f.owner ? `<dt>Suggested owner</dt><dd>${esc(f.owner)}</dd>` : ""}${f.moment || f.when ? `<dt>Suggested time</dt><dd>${esc(f.moment || f.when)}</dd>` : ""}
        ${f.how ? `<dt>How to check it works</dt><dd>${esc(f.how)}</dd>` : ""}${f.ifFails ? `<dt>If the check fails</dt><dd>${esc(f.ifFails)}</dd>` : ""}
        ${f.backup ? `<dt>Backup on the day</dt><dd>${esc(f.backup)}</dd>` : ""}${f.sign ? `<dt>Early sign</dt><dd>${esc(f.sign)}</dd>` : ""}</dl>
        ${f.risk ? `<p class="fix-risk">⚠ This fix may add a risk${f.riskNote ? `: ${esc(f.riskNote)}` : ""}</p>` : ""}</div>` : ""}
      ${f.kind !== "affected" && typeof f.clusterCount === "number" ? `<details class="rv-diag"><summary>Text grouping behind this finding (approximate)</summary>
        <p>${fmt(f.clusterCount)} worries were grouped by similar wording${f.clusterName ? ` under “${esc(f.clusterName)}”` : ""}. Not all of them describe this finding, so this is not a count of agreement.</p>
        ${(f.examples || []).length ? `<ul>${f.examples.slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</details>` : ""}
      ${gr && gr.n && !sp ? `<p class="rv-ground">${fmt(gr.n_quoted)} of ${fmt(gr.n)} worriers in this group quoted your plan; ${fmt(gr.n_omission)} named something it leaves out.</p>` : ""}
      <div class="rv-choices" role="radiogroup" aria-label="Your decision">
        <button type="button" role="radio" data-d="handled" aria-checked="${d === "handled"}">Already handled</button>
        <button type="button" role="radio" data-d="fix" aria-checked="${d === "fix"}" ${covered || !f.fix ? "disabled" : ""}>Needs this fix</button>
        <button type="button" role="radio" data-d="find" aria-checked="${d === "find"}" ${cd.question ? "" : "disabled"}>Need to find out</button>
      </div>
      <p class="rv-note" id="rv-note">${noteFor(f, d)}</p>`;
    $("rv-list").hidden = true; box.hidden = false; box.scrollTop = 0;
    box.querySelector(".rv-back").addEventListener("click", closeDetail);
    box.querySelectorAll(".rv-choices button").forEach((b) => b.addEventListener("click", (e) => {
      const v = b.dataset.d, on = S.dec.get(key) !== v;
      if (on) S.dec.set(key, v); else S.dec.delete(key);
      box.querySelectorAll(".rv-choices button").forEach((x) => x.setAttribute("aria-checked", String(on && x === b)));
      $("rv-note").innerHTML = noteFor(f, on ? v : null);
      if (on) { const r = e.currentTarget.getBoundingClientRect(); FX.burst(r.left + r.width / 2, r.top + 4, 26, 0.7); Sound.chime(); }
      if (f.kind === "top") Stage.sigh(f.key, on && v === "fix");
      syncRetest();
    }));
    if (f.kind === "top") Stage.focus(f.key);
    else { Stage.focus(null); const qc = f.seq != null ? Stage.creature(f.seq) : null; if (qc) G.fromTo(qc, { pop: 0.6 }, { pop: 0, duration: D(0.8), ease: "elastic.out(1,.4)" }); }
    if (!reduce) G.fromTo(box, { x: 24, opacity: 0 }, { x: 0, opacity: 1, duration: 0.35, ease: "power2.out", clearProps: "transform,opacity" });
  }
  function noteFor(f, d) {
    if (d === "handled") return `Recorded as your report, not as evidence. It stays on the Plan Card as “reported handled · not verified”.${f.how ? ` To check it: ${esc(f.how)}` : ""}`;
    if (d === "fix") return "Added to your revised plan as a planned change. It is not done until you do it.";
    if (d === "find") return "Added to your revised plan as a check to make before the day.";
    return "";
  }
  function closeDetail() {
    S.open = null; $("rv-detail").hidden = true; $("rv-list").hidden = false; Stage.focus(null);
    renderList();
  }
  // click a huddle on the stage to open its finding
  Stage.el.addEventListener("click", (e) => {
    if (S.phase !== "fixes" || !el.calm.hidden) return;
    if (e.target.closest("button, a, input, #quiet, #results, #calm")) return;   // a button over the stage is not a huddle click
    const r = Stage.el.getBoundingClientRect(), id = Stage.huddleAt(e.clientX - r.left, e.clientY - r.top);
    if (id) detail(id);
  });

  function decided() {
    const by = (v) => S.items.filter((f) => S.dec.get(f.key) === v);
    return { fix: by("fix"), find: by("find"), handled: by("handled") };
  }
  function syncRetest() {
    const d = decided(), k = d.fix.length + d.find.length;
    el.retest.disabled = !k;
    el.retest.querySelector("span").textContent = k ? "Build my revised plan" : "Decide on at least one finding";
    $("rv-count").textContent = [d.fix.length && `${d.fix.length} fix${d.fix.length > 1 ? "es" : ""}`, d.find.length && `${d.find.length} to find out`,
      d.handled.length && `${d.handled.length} reported handled`].filter(Boolean).join(" · ") || "No decisions yet.";
  }
  const changesOf = (d) => d.fix.map((f) => f.fix);
  const checksOf = (d) => d.find.map((f) => `Find out before the day: ${f.card.question}`);
  const reportsOf = (d) => d.handled.map((f) => `${f.name}: the planner says this is already handled (not verified, no evidence given).`);

  el.retest.addEventListener("click", async () => {
    const d = decided();
    if (!(d.fix.length + d.find.length) || !S.run) return;
    el.retest.disabled = true; msg(el.retestMsg, "");
    el.retest.querySelector("span").textContent = "Nova Pro is merging your changes…";
    try {
      S.revise = await API.revise(S.run.id, { changes: changesOf(d), checks: d.find.map((f) => f.card.question), reports: reportsOf(d) });
      S.decSnap = d;
      calmPlan();
    } catch (e) { msg(el.retestMsg, e.message); }
    syncRetest();
  });

  /* ----- phase 6: re-test. The revised plan goes back to the same worriers; reports travel separately, labelled as unverified. ----- */
  async function launchRetest() {
    const d = S.decSnap || decided();
    const go = $("pc-retest"); if (go) go.disabled = true;
    try {
      // re-test exactly the saved revision the Plan Card showed; older payload only if the revision was not saved
      const body = S.revise && S.revise.revision_id && S.revise.persisted ? { parent_run_id: S.run.id, revision_id: S.revise.revision_id }
        : { parent_run_id: S.run.id, accepted_fixes: [...changesOf(d), ...checksOf(d)], planner_reports: reportsOf(d) };
      const { run_id } = await API.start(body);
      el.calm.hidden = true;
      startRetest(run_id);
    } catch (e) { const n = $("pc-msg"); if (n) { n.textContent = e.message; n.hidden = false; } if (go) go.disabled = false; }
  }

  async function startRetest(id) {
    const parent = S.run;
    resetUI();
    const token = S.token;
    S.parent = parent;
    S.run = { id, n: parent.n, status: "STARTING", isRetest: true, result: null, labels: null, plan: "" };
    S.castDone = true; S.phase = "retest";
    setPhase("run"); el.fixpanel.hidden = true;
    history.replaceState(null, "", `${location.pathname}${location.search}#run=${id}`);
    Status.set("Asking them again…");
    Ticker.empty("Same worriers, revised plan…");
    Stats.reset(); Stats.times(Date.now(), 0);
    Meter.live(true); Meter.text(0, parent.n, "Same worriers, new plan.");
    await wait(220);
    await Stage.home(1.1);
    if (token !== S.token) return;
    Status.rotate(() => pick([`${fmt(S.spoken.size)} of ${fmt(S.run.n)} have looked again`, "Re-reading your plan…", "Checking each risk against your changes…", "Looking for anything new…"]), 2100);
    startDrain();
    poll(token);
  }

  async function retestFinale(token) {
    const r = S.run, p = S.parent, res = r.result;
    S.phase = "results"; Status.stop(); Meter.live(false);
    const topIds = new Set(p.result.top5.map((t) => t.id));
    const calmers = Stage.cs.filter((c) => !topIds.has((r.labels || {})[String(c.seq)])).sort(() => Math.random() - 0.5);
    Status.set("They have read your revised plan.");
    const per = Math.max(1, Math.ceil(calmers.length / 70));
    for (let i = 0; i < calmers.length; i += per) {
      if (token !== S.token) return;
      calmers.slice(i, i + per).forEach((c) => Stage.calmDown(c));
      if (!reduce) await new Promise((rs) => setTimeout(rs, 35));
    }
    const cmp = res.comparison || [];
    const rows = p.result.top5.map((t) => cmp.find((c) => c.id === t.id) || { id: t.id, name: t.name, before: t.count, after: 0 });
    const before = rows.reduce((a, c) => a + c.before, 0), after = rows.reduce((a, c) => a + c.after, 0);
    Meter.set(after / (res.per1000 ? 1000 : res.n_worries), 1.6);
    Meter.label("in the old top-five groups"); Meter.text(after, res.per1000 ? 1000 : res.n_worries, `${fmt(before)} in the first run. Grouped by wording; not a measure of risk.`);
    await wait(800);
    if (token !== S.token) return;
    showResults(rows);
  }

  const RT = {
    addressed_in_plan: ["Addressed in the plan (proposed, not done yet)", "st-cov"],
    partially_addressed: ["Partly addressed (proposed, not done yet)", "st-part"],
    check_planned: ["Check planned · outcome unknown", "st-unv"],
    unresolved: ["Still open", "st-open"],
    reported_covered_unverified: ["Reported by you · not verified", "st-unv"],
  };
  function showResults(rows) {
    const res = S.run.result, p = S.parent.result;
    const items = buildItems(p);
    const as = res.assessments || {};
    const verd = {};
    (res.comparison || []).forEach((c) => { if (c.verdict) verd[c.name] = c.verdict; });
    const qa = res.quiet, aa = res.affected;
    const room = [
      ...(aa && typeof aa.before === "number" ? [[items[0] && items[0].kind === "affected" ? items[0].name : "What they want", aa.before, aa.count, `of ${fmt(aa.of_before)} → ${fmt(aa.of)} asked`]] : []),
      ...rows.map((c) => [c.name, c.before, c.after, verd[c.name] ? (verd[c.name] === "real" ? (c.after < c.before ? "fell more than the unchanged-plan run moved" : "rose more than the unchanged-plan run moved") : "within the unchanged-plan run's movement") : ""]),
      ...(qa && typeof qa.before === "number" ? [["The quiet worry", qa.before, qa.count, ""]] : []),
    ];
    el.results.innerHTML = `
      <h2>Re-test: each finding against your plan with proposed amendments</h2>
      <p class="sub">Amazon Nova Lite judged each original finding against your plan and the changes you accepted, which are planned, not done. Your own reports count as your word, never as evidence. Nothing here says the plan is safe.</p>
      <div class="rt-list">${items.map((f) => {
        const a = as[f.key];
        if (!a) return `<div class="rt-row" style="--c:${f.color}"><div class="rt-name">${esc(f.name)}</div><i class="tg">Not assessed</i><p>The check for this finding did not run, so no verdict is given.</p></div>`;
        const t = RT[a.assessment] || RT.unresolved;
        return `<div class="rt-row" style="--c:${f.color}"><div class="rt-name">${esc(f.name)}</div><i class="tg ${t[1]}">${t[0]}</i>
          <p>${esc(a.reason)}${a.basis ? ` <span class="rt-basis">“${esc(a.basis)}”</span>` : ""}${a.still_open ? `<br><b>Still to do:</b> ${esc(a.still_open)}` : ""}</p></div>`;
      }).join("") || `<p class="sub">No assessments came back for this re-test.</p>`}</div>
      ${(res.new_concerns || []).length ? `<h3>Appeared after the revision</h3><ul class="rt-new">${res.new_concerns.map((c) => `<li><b>${esc(c.concern)}</b> ${esc(c.mechanism)}
        <em>${c.related_change ? `Near your change: “${esc(c.related_change)}”. Not proof the change causes it.` : "New in this run; no cause claimed."}</em></li>`).join("")}</ul>` : ""}
      <details class="rt-room"><summary>How the text groupings moved (approximate counts per 1,000 worriers, grouped by wording; not agreement, not proof of lower risk)</summary>
        <table><thead><tr><th></th><th>before</th><th>after</th><th></th></tr></thead><tbody>
        ${room.map(([n, b, a, v]) => `<tr><td>${esc(n)}</td><td>${fmt(b)}</td><td>${fmt(a)}</td><td>${esc(v)}</td></tr>`).join("")}</tbody></table>
        <p class="pc-fine">${res.noise_checked ? "Compared with a run of the unchanged plan by the same worriers." : "No unchanged-plan run for comparison."} ${res.new_worries ? `${fmt(res.new_worries)} worries matched no earlier huddle.` : ""}</p>
      </details>
      <div class="res-actions"><button class="pill pill-calm" type="button" id="to-calm">See your Plan Card</button></div>`;
    el.results.hidden = false;
    G.fromTo(el.results, { y: 30, opacity: 0, scale: 0.96 }, { y: 0, opacity: 1, scale: 1, duration: D(0.5), ease: "back.out(1.6)" });
    if (!reduce) G.from(el.results.querySelectorAll(".rt-row"), { x: -20, opacity: 0, stagger: 0.12, duration: 0.4, delay: 0.3, ease: "power2.out", clearProps: "transform,opacity" });
    $("to-calm").addEventListener("click", () => calmPlan());
    Status.set("Re-test done. Read each finding's verdict.");
  }

  /* ----- phase 7: the Plan Card. "Before you go ahead" first, then the revised plan. ----- */
  function calmPlan() {
    const retested = !!(S.run && S.run.isRetest);
    const home = retested ? S.parent : S.run;
    el.results.hidden = true;
    const plan = basePlan(home.plan);
    const title = (plan.split(/(?<=[.!?;])\s+/)[0] || "Your plan").replace(/[.;]$/, "");
    if (!S.items || !S.items.length) S.items = buildItems(home.result);
    const d = S.decSnap || decided();
    const rv = S.revise || {};
    const res = (S.run && S.run.result) || {};
    const revised = (rv.bullets && rv.bullets.length ? rv.bullets : res.revised_plan) || [];
    const conflicts = rv.conflicts || [];
    // The canonical revision (Revise workflow). Everything below, the copy text and the re-test read these same fields.
    const led = rv.revision_id ? rv : null;
    const detailOf = (txt) => d.fix.find((f) => cleanFix(f.fix).trim().toLowerCase() === String(txt).trim().toLowerCase()) || {};
    const chg = led ? led.accepted_changes.map((c) => ({ ...detailOf(c.action), fix: c.action })) : d.fix;
    const qs = led ? led.open_questions.map((q) => q.question) : d.find.map((f) => f.card.question);
    const reps = led ? led.planner_reports.map((r) => r.text) : d.handled.map((f) => f.name);
    const signs = d.fix.filter((f) => f.sign);
    const pad = (n) => String(n).padStart(2, "0");
    const status = retested ? "Re-tested against your plan with proposed amendments · nothing is done or verified yet" : "Proposed amendments · nothing is done or verified yet";
    el.calm.innerHTML = `
      <div class="pc-head">
        <div><p class="pc-eyebrow">Plan Card</p><h2>${esc(title)}</h2></div>
        <div class="pc-actions"><button class="pill" type="button" id="pc-copy">Copy</button><button class="pill" type="button" id="pc-png">Download</button><button class="pill" type="button" id="calm-new">New plan</button></div>
      </div>
      <section class="pc-ahead"><h3>Before you go ahead</h3>
        <ol class="pc-sum">
          <li><b>Changes proposed</b>${chg.length ? `${chg.length} accepted change${chg.length > 1 ? "s are" : " is"} proposed below. None is done yet.` : "No changes accepted."}</li>
          <li><b>Resolve first</b>${conflicts.length || qs.length ? [conflicts.length && `${conflicts.length} conflict${conflicts.length > 1 ? "s" : ""} to decide`, qs.length && `${qs.length} open question${qs.length > 1 ? "s" : ""} (not safeguards)`].filter(Boolean).join(" and ") + "." : "Nothing flagged."}</li>
          <li><b>Verify</b>Each change still needs its check done before it counts as working.</li>
          ${reps.length ? `<li><b>Confirm</b>${reps.length} finding${reps.length > 1 ? "s" : ""} you reported as handled, not verified.</li>` : ""}
        </ol>
        ${chg.length ? `<div><b>Change</b><ul>${chg.map((f) => `<li>${esc(cleanFix(f.fix))}${f.owner || f.moment ? ` <em>(suggested: ${esc([f.owner, f.moment || f.when].filter(Boolean).join(", "))})</em>` : ""}${f.how ? `<span class="pc-how">Check it works: ${esc(f.how)}</span>` : ""}</li>`).join("")}</ul></div>` : ""}
        ${qs.length ? `<div><b>Find out (open questions, not safeguards)</b><ul>${qs.map((q) => `<li>${esc(q)}</li>`).join("")}</ul></div>` : ""}
        ${reps.length ? `<div><b>Reported handled by you · not verified</b><ul>${reps.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
        ${signs.length ? `<div><b>Watch</b><ul>${signs.map((f) => `<li>${esc(f.sign)}${f.when ? ` <em>(${esc(f.when)})</em>` : ""}</li>`).join("")}</ul></div>` : ""}
        <div><b>Conflicts and questions to decide</b>${conflicts.length ? `<ul class="pc-conf">${conflicts.map((c) => `<li>${c.kind === "question" ? "<b>To decide:</b> " : ""}${(c.between || []).length ? `<i>${c.between.map(esc).join(" ↔ ")}</i>: ` : ""}${esc(c.why)}</li>`).join("")}</ul>` : `<p>${rv.bullets ? "None found between your changes and your plan." : "Not checked yet."}</p>`}</div>
        <p class="pc-status">${status}</p>
      </section>
      <div class="pc-grid">
        ${led ? `<section><h3>Your plan with proposed amendments</h3>
          <ul class="pc-plan">${led.plan_lines.map((l) => l.status === "replaced"
            ? `<li class="added"><span class="pc-old">${esc(l.original)}</span><span class="pc-upd">Proposed: ${esc(l.text)}</span></li>`
            : l.status === "unresolved" ? `<li class="updated">${esc(l.text)}<span class="pc-upd">A change alters this line; decide which one stands (see Conflicts).</span></li>`
            : `<li>${esc(l.text)}</li>`).join("")}</ul>
          ${led.accepted_changes.length ? `<p class="pc-sub">Proposed changes (accepted, not done yet)</p><ul class="pc-plan">${led.accepted_changes.map((c) => `<li class="added">${esc(c.action)}</li>`).join("")}</ul>` : ""}
          ${led.open_questions.length ? `<p class="pc-sub">Open questions (not answered yet)</p><ul class="pc-plan">${led.open_questions.map((q) => `<li>${esc(q.question)}</li>`).join("")}</ul>` : ""}
          <p class="pc-fine">Your original lines, word for word${led.superseded_items.length ? ", with the exact replacements shown" : ""}. A line is rewritten only for an exact change of a number or time; check that no other line still contradicts your changes. Nova Pro only ordered your changes and checked for conflicts. The re-test reads exactly this text${led.persisted ? ` (revision ${esc(String(led.revision_id).slice(0, 8))})` : ""}.</p>
        </section>` : `<section><h3>Your revised plan</h3>
          <ul class="pc-plan">${revised.map((b) => `<li class="${b.changed ? "added" : ""}${b.updated_by ? " updated" : ""}">${b.updated_by ? `<span class="pc-old">${esc(b.text)}</span>` : esc(b.text)}${b.updated_by ? `<span class="pc-upd">Changed by: ${esc(cleanFix(b.updated_by))}</span>` : ""}</li>`).join("") || `<li>${esc(plan)}</li>`}</ul>
          <p class="pc-fine">${revised.length ? "Merged by Amazon Nova Pro from your plan and only the changes and checks you chose. Changes are planned, not done." : ""}</p>
        </section>`}
        <section>${d.fix.some((f) => f.backup) ? `<h3>If it still goes wrong</h3><ul class="pc-back">${d.fix.filter((f) => f.backup).map((f) => `<li>${esc(f.backup)}</li>`).join("")}</ul>` : ""}
          ${!retested ? `<div class="pc-retest"><button class="summon calm" type="button" id="pc-retest"><span>Re-test with these changes</span></button>
            <p class="pc-fine">The same worriers read the plan above, with its proposed amendments; each finding gets a verdict.</p><p class="msg" id="pc-msg" hidden></p></div>` : ""}
        </section>
      </div>
      ${signs.length ? `<h3>Remind me to check the early signs</h3>
      <form class="remind" id="remind">
        <div class="r-top">
          <label>Email <input type="email" id="r-email" required placeholder="you@example.com"></label>
          <label>Main event <input type="date" id="r-event" required></label>
        </div>
        ${signs.map((c, i) => `<div class="r-item"><label><input type="checkbox" class="r-on" data-i="${i}" checked> <span>${esc(c.sign)}${c.when ? ` <em>(${esc(c.when)})</em>` : ""}</span></label>
          <input type="datetime-local" class="r-at" data-i="${i}" aria-label="When to remind me"></div>`).join("")}
        <label class="r-test"><input type="checkbox" id="r-test"> Also send a test reminder in 2 minutes</label>
        <button class="pill pill-calm" type="submit" id="r-go">Remind me</button>
        <p class="note" id="remind-note" hidden></p>
        <ul class="r-done" id="r-done" hidden></ul>
        <p class="r-fine">Nothing is sent until you press Remind me. Demo mode (Amazon SES sandbox): reminders only go to this account's verified address. Each one is a one-time schedule that deletes itself after sending.</p>
      </form>` : ""}`;
    el.calm.hidden = false; el.calm.scrollTop = 0;
    G.fromTo(el.calm, { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: D(0.5), ease: "back.out(1.4)" });
    Status.set(retested ? "Your Plan Card, after the re-test." : "Your Plan Card. Read “Before you go ahead” first.");
    $("calm-new").addEventListener("click", idle);
    if ($("pc-retest")) $("pc-retest").addEventListener("click", launchRetest);
    $("pc-copy").addEventListener("click", async (e) => {
      const txt = [`PLAN CARD: ${title}`, "", "BEFORE YOU GO AHEAD",
        ...(chg.length ? ["Change (proposed, not done):", ...chg.map((f) => `- ${cleanFix(f.fix)}${f.owner ? ` (suggested owner: ${f.owner})` : ""}${f.how ? ` | check: ${f.how}` : ""}`)] : []),
        ...(qs.length ? ["Find out (open questions, not safeguards):", ...qs.map((q) => `- ${q}`)] : []),
        ...(reps.length ? ["Reported handled by me (not verified):", ...reps.map((r) => `- ${r}`)] : []),
        ...(signs.length ? ["Watch:", ...signs.map((f) => `- ${f.sign}`)] : []),
        ...(conflicts.length ? ["Conflicts:", ...conflicts.map((c) => `- ${c.why}`)] : []),
        `Status: ${status}`, "", led ? "PLAN WITH PROPOSED AMENDMENTS (the exact text the re-test reads)" : "REVISED PLAN",
        ...(led ? [led.effective_plan_text] : revised.map((b) => `${b.changed ? "+" : "•"} ${b.text}`)), "",
        ...(d.fix.some((f) => f.backup) ? ["IF IT STILL GOES WRONG", ...d.fix.filter((f) => f.backup).map((f) => `- ${f.backup}`), ""] : []),
        "Made with 1,000 Worriers. Worriers are angles, not people. These are possible failures, not probabilities."].join("\n");
      try { await navigator.clipboard.writeText(txt); e.currentTarget.textContent = "Copied ✓"; } catch (_) { e.currentTarget.textContent = "Copy failed"; }
    });
    $("pc-png").addEventListener("click", async (e) => {
      const b = e.currentTarget; if (!window.html2canvas) { b.textContent = "Not ready"; return; }
      b.textContent = "Rendering…";
      const hide = [$("remind"), el.calm.querySelector(".pc-retest")].filter(Boolean); hide.forEach((x) => { x.style.display = "none"; });
      const canvas = await window.html2canvas(el.calm, { backgroundColor: "#140F35", scale: 2, height: el.calm.scrollHeight, windowHeight: el.calm.scrollHeight });
      hide.forEach((x) => { x.style.display = ""; });
      const a = document.createElement("a"); a.download = "plan-card.png"; a.href = canvas.toDataURL("image/png"); a.click();
      b.textContent = "Downloaded ✓";
    });
    if (!signs.length) return;
    const local = (dt) => `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    const ev = new Date(); ev.setDate(ev.getDate() + 7);
    $("r-event").value = `${ev.getFullYear()}-${pad(ev.getMonth() + 1)}-${pad(ev.getDate())}`;
    const touched = new Set();
    const suggest = () => {
      const [y, mo, dd] = $("r-event").value.split("-").map(Number);
      if (!y) return;
      el.calm.querySelectorAll(".r-at").forEach((inp) => {
        const i = +inp.dataset.i; if (touched.has(i)) return;
        const t = new Date(y, mo - 1, dd, 9, 0); t.setDate(t.getDate() - (signs[i].days ?? 2));
        const min = new Date(Date.now() + 3600e3); inp.value = local(t < min ? min : t);
      });
    };
    suggest();
    $("r-event").addEventListener("change", suggest);
    el.calm.querySelectorAll(".r-at").forEach((inp) => inp.addEventListener("input", () => touched.add(+inp.dataset.i)));
    $("remind").addEventListener("submit", async (e) => {
      e.preventDefault();
      const note = $("remind-note"), done = $("r-done"), go = $("r-go");
      const reminders = [];
      el.calm.querySelectorAll(".r-on").forEach((cb) => {
        if (!cb.checked) return;
        const i = +cb.dataset.i, at = el.calm.querySelector(`.r-at[data-i="${i}"]`).value;
        if (at) reminders.push({ text: signs[i].sign, fix: signs[i].fix, at: new Date(at).toISOString() });
      });
      if ($("r-test").checked) reminders.unshift({ text: `Test reminder: ${signs[0] ? signs[0].sign : "your 1,000 Worriers plan"}`, fix: signs[0] ? signs[0].fix : "", at: new Date(Date.now() + 120e3).toISOString() });
      if (!reminders.length) { note.textContent = "Pick at least one reminder."; note.hidden = false; return; }
      go.disabled = true; note.hidden = true;
      try {
        const out = await API.reminders(home.id, { email: $("r-email").value.trim(), reminders: reminders.slice(0, 6) });
        if (out.error) throw new Error(out.cause || out.error);
        done.innerHTML = out.scheduled.map((x) => `<li>Scheduled ✓ <b>${esc(new Date(x.at).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</b> · ${esc(x.text)}</li>`).join("");
        done.hidden = false; go.textContent = "Scheduled ✓"; Sound.chime();
        const r = go.getBoundingClientRect(); FX.burst(r.left + r.width / 2, r.top, 40);
      } catch (err) {
        note.textContent = /BadRequest|valid email/.test(err.message) ? "Check the email address and that every time is in the future." : `Couldn't schedule: ${err.message}`;
        note.hidden = false; go.disabled = false;
      }
    });
  }

  /* ----- hover cards ----- */
  Stage.el.addEventListener("mousemove", (e) => {
    if (S.phase === "empty" || !el.results.hidden || !el.calm.hidden) { el.hover.hidden = true; return; }
    const r = Stage.el.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const c = Stage.at(x, y);
    if (!c) { el.hover.hidden = true; Stage.el.style.cursor = ""; return; }
    Stage.el.style.cursor = "pointer";
    showHover(c, x, y);
  });
  function showHover(c, x, y) {
    const w = c.w, r = Stage.el.getBoundingClientRect();
    const sev = w ? Math.max(1, Math.min(5, w.severity || 1)) : 0;
    el.hover.innerHTML = w ? `
      <div class="h-role">${esc(w.character)}</div><div class="h-group">${esc(w.group || "")}</div>
      <div class="h-meta"><span>${esc(w.angle)}</span><span>${esc(w.moment)}</span></div>
      <p class="h-worry">“${esc(w.failure)}”</p>
      <div>Worry level <span class="pips">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= sev ? "on" : ""}"></i>`).join("")}</span></div>`
      : `<div class="h-role">Worrier #${c.seq}</div><p class="h-worry">Still thinking…</p>`;
    el.hover.hidden = false;
    const hx = x + 18 + 250 > r.width ? x - 268 : x + 18;
    const hy = Math.max(70, Math.min(r.height - el.hover.offsetHeight - 10, y - 20));
    el.hover.style.left = `${hx}px`; el.hover.style.top = `${hy}px`;
  }
  Stage.el.addEventListener("mouseleave", () => { el.hover.hidden = true; });

  /* ----- resume from a link (#run=...) ----- */
  async function resume(id) {
    try {
      const meta = await API.run(id);
      if (meta.parent_run_id) {
        const pm = await API.run(meta.parent_run_id);
        const pw = (await API.worries(pm.run_id, 1000)).worries;
        resetUI();
        el.empty.hidden = true; document.querySelector(".status-chip").hidden = false;
        const parent = { id: pm.run_id, n: pm.n, result: pm.result, labels: pm.labels || {}, plan: pm.plan, status: "DONE" };
        S.run = parent; S.phase = "gathering";
        Stage.build(pm.n); Stage.setGroups(((pm.grid && pm.grid.characters) || []).map((c) => c.group));
        Stage.cs.forEach((c) => { c.a = 1; });
        pw.forEach((w) => { const c = Stage.creature(seqOf(w)); if (c) { c.w = w; Stage.costume(c, w.group); c.expr = C.exprFor(w.severity); } });
        el.plan.value = pm.plan || ""; setCrowd(pm.n);
        S.items = buildItems(pm.result);
        const fx = meta.plan ? (meta.plan.split("Changes made to the plan:\n- ")[1] || "").split("\n- ").filter(Boolean) : [];
        S.items.forEach((f) => { if (fx.includes(f.fix)) S.dec.set(f.key, "fix"); else if (f.card && fx.includes(`Find out before the day: ${f.card.question}`)) S.dec.set(f.key, "find"); });
        setPhase("review");
        await wait(260);
        await Stage.gather(pm.result.top5, parent.labels, pm.result.n_worries, true);
        startRetest(id);
        return;
      }
      el.plan.value = meta.plan || ""; setCrowd(meta.n);
      begin(id, { n: meta.n, plan: meta.plan, replay: meta.status === "DONE" });
    } catch (e) {
      idle();
    }
  }

  // test hook for screenshots: jump straight to a phase of a finished run
  window.__show = { get debug() { return { queue: S.queue.length, full: S.fullLoaded, status: S.run && S.run.status, n: S.run && S.run.n, castDone: S.castDone, missing: Stage.cs.filter((c) => !S.spoken.has(c.seq)).slice(0, 8).map((c) => c.seq) }; }, get phase() { return S.phase; }, get dec() { return [...S.dec.entries()]; }, get spoken() { return S.spoken.size; }, get casting() { return S.casting; }, hover(seq) { const c = Stage.creature(seq); if (c) { const r = Stage.el.getBoundingClientRect(); showHover(c, c.x, c.y); } } };

  /* ----- boot ----- */
  /* ----- info popover ----- */
  $("info").addEventListener("click", (e) => {
    const pop = $("info-pop"), open = pop.hidden;
    pop.hidden = !open; e.currentTarget.setAttribute("aria-expanded", String(open));
    if (open) G.fromTo(pop, { y: -6, opacity: 0 }, { y: 0, opacity: 1, duration: D(0.25) });
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".tools")) { $("info-pop").hidden = true; $("info").setAttribute("aria-expanded", "false"); } });

  const m = location.hash.match(/run=([\w-]{8,64})/); // read before idle() clears the URL
  idle();
  if (m && !MOCK) resume(m[1]);
  if (MOCK && params.get("auto") === "1") { el.plan.value = DEMOS.proposal; S.n = MOCK; el.go.click(); }
})();
