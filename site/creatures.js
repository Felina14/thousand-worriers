/* Procedural creatures for the Worry Theatre. Original art drawn in code.
   Each (colour, prop, expression, blink) combination is rendered once to an offscreen sprite,
   so the stage can draw 1,000 of them per frame with drawImage. */
(() => {
  "use strict";
  const INK = "#1B1035";   // face features only; bodies have no outline
  const PALETTE = ["#FF4FA3", "#8B5CF6", "#A3E635", "#22D3EE", "#FB923C", "#38BDF8", "#F472B6", "#FACC15", "#34D399", "#C084FC", "#FF6B6B", "#60A5FA"];
  const UNDERSTUDY = "#5A5390";
  const BASE = 96;      // design units
  const PX = 128;       // sprite pixels
  const cache = new Map();

  function rng(seed) { let s = seed * 9301 + 49297; return () => ((s = (s * 9301 + 49297) % 233280) / 233280); }

  /* Which tiny prop a stakeholder group gets. Group names come from the plan, so this is a keyword guess. */
  function propFor(group, gi) {
    const g = String(group || "").toLowerCase();
    if (gi === 0) return "bow";
    if (/planner|organi[sz]er|\bme\b|myself|yourself|you\b/.test(g)) return "clipboard";
    if (/family|parent|mother|father|mom|dad|sister|brother|aunt|uncle|grand|kid|child|household|relative|in-law|spouse|wife|husband/.test(g)) return "hat";
    if (/expert|forecast|weather|doctor|mechanic|lawyer|advisor|adviser|nerd|official|police|authority|bank|lender|inspector|rule/.test(g)) return "glasses";
    if (/restaurant|bar|vendor|stall|shop|seller|driver|taxi|staff|hotel|homestay|venue|service|cafe|café|host|waiter|airline|rail|train|app/.test(g)) return "apron";
    if (/friend|peer|colleague|classmate|mate|group|guest/.test(g)) return "phone";
    return "none";
  }

  function blob(ctx, cx, cy, r, seed) {
    const rand = rng(seed + 7);
    const pts = [];
    const k = 9;
    for (let i = 0; i < k; i++) {
      const a = (i / k) * Math.PI * 2 - Math.PI / 2;
      const rr = r * (0.94 + rand() * 0.12) * (Math.sin(a) > 0.3 ? 0.97 : 1);
      pts.push([cx + Math.cos(a) * rr * 1.02, cy + Math.sin(a) * rr * (Math.sin(a) > 0 ? 0.98 : 1.08)]);
    }
    ctx.beginPath();
    for (let i = 0; i <= k; i++) {
      const p = pts[i % k], q = pts[(i + 1) % k];
      const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
      if (i === 0) ctx.moveTo(mx, my); else ctx.quadraticCurveTo(p[0], p[1], mx, my);
    }
    ctx.closePath();
  }

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.max(0, Math.min(255, Math.round(v + amt * 255)));
    return `rgb(${c(n >> 16)},${c((n >> 8) & 255)},${c(n & 255)})`;
  }

  function eyes(ctx, expr, blink) {
    ctx.fillStyle = INK; ctx.strokeStyle = INK; ctx.lineCap = "round"; ctx.lineJoin = "round";
    const L = 37, R = 59, Y = 47;
    if (expr === "sleep" || (blink && (expr === "calm" || expr === "uneasy"))) {
      ctx.lineWidth = 3;
      ctx.beginPath();
      if (expr === "sleep") { ctx.arc(L, Y - 1, 5, 0.15 * Math.PI, 0.85 * Math.PI); ctx.moveTo(R + 5, Y); ctx.arc(R, Y - 1, 5, 0.15 * Math.PI, 0.85 * Math.PI); }
      else { ctx.moveTo(L - 4, Y); ctx.lineTo(L + 4, Y); ctx.moveTo(R - 4, Y); ctx.lineTo(R + 4, Y); }
      ctx.stroke();
      return;
    }
    if (expr === "calm" || expr === "uneasy") {
      ctx.beginPath(); ctx.arc(L, Y, 4, 0, 7); ctx.arc(R, Y, 4, 0, 7); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(L + 1.4, Y - 1.4, 1.3, 0, 7); ctx.arc(R + 1.4, Y - 1.4, 1.3, 0, 7); ctx.fill();
      if (expr === "uneasy") { ctx.lineWidth = 2.6; ctx.beginPath(); ctx.moveTo(R - 6, Y - 10); ctx.lineTo(R + 5, Y - 8); ctx.stroke(); }
      return;
    }
    if (expr === "worried") {
      ctx.lineWidth = 2.6; ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.arc(L, Y, 8, 0, 7); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.arc(R, Y, 8, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(L + 1, Y - 2, 3.4, 0, 7); ctx.arc(R - 1, Y - 2, 3.4, 0, 7); ctx.fill();
      ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(L - 8, Y - 11); ctx.lineTo(L + 5, Y - 15); ctx.moveTo(R + 8, Y - 11); ctx.lineTo(R - 5, Y - 15); ctx.stroke();
      return;
    }
    // panic: spiral eyes
    ctx.lineWidth = 2.2; ctx.fillStyle = "#fff";
    for (const x of [L, R]) {
      ctx.beginPath(); ctx.arc(x, Y, 9, 0, 7); ctx.fill(); ctx.stroke();
      ctx.beginPath();
      for (let t = 0; t < 4.2 * Math.PI; t += 0.25) { const rr = 0.6 + t * 0.55; ctx.lineTo(x + Math.cos(t) * rr, Y + Math.sin(t) * rr); }
      ctx.stroke();
    }
    ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(L - 9, Y - 13); ctx.lineTo(L + 6, Y - 18); ctx.moveTo(R + 9, Y - 13); ctx.lineTo(R - 6, Y - 18); ctx.stroke();
  }

  function mouth(ctx, expr) {
    ctx.strokeStyle = INK; ctx.fillStyle = INK; ctx.lineWidth = 3; ctx.lineCap = "round";
    ctx.beginPath();
    if (expr === "calm") { ctx.arc(48, 58, 9, 0.18 * Math.PI, 0.82 * Math.PI); ctx.stroke(); }
    else if (expr === "sleep") { ctx.arc(48, 64, 3.2, 0, 7); ctx.stroke(); }
    else if (expr === "uneasy") { ctx.moveTo(40, 65); ctx.lineTo(56, 63); ctx.stroke(); }
    else if (expr === "worried") {
      ctx.moveTo(38, 67); ctx.quadraticCurveTo(41, 62, 44, 66); ctx.quadraticCurveTo(47, 70, 50, 66); ctx.quadraticCurveTo(53, 62, 58, 66); ctx.stroke();
    } else {
      ctx.ellipse(48, 67, 7.5, 9.5, 0, 0, 7); ctx.fill();
      ctx.fillStyle = "#FF5A5F"; ctx.beginPath(); ctx.ellipse(48, 72, 4.5, 3.4, 0, 0, 7); ctx.fill();
    }
  }

  function drop(ctx, x, y, s) {
    const g = ctx.createLinearGradient(x, y - 7 * s, x, y + 4 * s);
    g.addColorStop(0, "#E0F7FF"); g.addColorStop(1, "#38BDF8");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.moveTo(x, y - 7 * s); ctx.quadraticCurveTo(x + 6 * s, y + 2 * s, x, y + 4 * s); ctx.quadraticCurveTo(x - 6 * s, y + 2 * s, x, y - 7 * s); ctx.fill();
  }

  function prop(ctx, p, body) {
    ctx.strokeStyle = "rgba(27,16,53,.55)"; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (p === "hat") {
      const hg = ctx.createLinearGradient(35, 3, 61, 21); hg.addColorStop(0, "#4C3F7A"); hg.addColorStop(1, "#1E1640");
      ctx.fillStyle = hg; ctx.beginPath(); ctx.ellipse(48, 21, 21, 5, 0, 0, 7); ctx.fill();
      ctx.beginPath(); ctx.roundRect(35, 3, 26, 18, 4); ctx.fill();
      ctx.fillStyle = "#FF3D71"; ctx.fillRect(35, 14, 26, 4);
      ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.fillRect(38, 5, 4, 8);
    } else if (p === "bow") {
      ctx.fillStyle = "#FF3D71";
      ctx.beginPath(); ctx.moveTo(62, 20); ctx.lineTo(50, 12); ctx.lineTo(51, 28); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(62, 20); ctx.lineTo(75, 11); ctx.lineTo(74, 28); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.arc(62, 20, 4, 0, 7); ctx.fill(); ctx.stroke();
    } else if (p === "glasses") {
      ctx.strokeStyle = "#1B1035"; ctx.lineWidth = 2.4; ctx.fillStyle = "rgba(200,240,255,.25)";
      ctx.beginPath(); ctx.arc(37, 47, 10, 0, 7); ctx.fill(); ctx.stroke(); ctx.beginPath(); ctx.arc(59, 47, 10, 0, 7); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(47, 46); ctx.quadraticCurveTo(48, 43, 49, 46); ctx.stroke();
    } else if (p === "phone") {
      ctx.fillStyle = body; ctx.beginPath(); ctx.ellipse(80, 66, 7, 6, 0, 0, 7); ctx.fill();
      ctx.fillStyle = "#1E1640"; ctx.beginPath(); ctx.roundRect(76, 46, 12, 20, 3); ctx.fill();
      ctx.fillStyle = "#22D3EE"; ctx.fillRect(78.5, 49, 7, 12);
    } else if (p === "apron") {
      ctx.fillStyle = "#FFFDF7";
      ctx.beginPath(); ctx.moveTo(34, 70); ctx.lineTo(62, 70); ctx.lineTo(60, 86); ctx.quadraticCurveTo(48, 90, 36, 86); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(35, 70); ctx.lineTo(30, 60); ctx.moveTo(61, 70); ctx.lineTo(66, 60); ctx.stroke();
      ctx.beginPath(); ctx.roundRect(43, 76, 10, 6, 2); ctx.stroke();
    } else if (p === "clipboard") {
      ctx.fillStyle = "#FACC15"; ctx.beginPath(); ctx.roundRect(4, 52, 20, 27, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.fillRect(7.5, 57, 13, 18);
      ctx.strokeStyle = "#8B5CF6"; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.moveTo(9, 62); ctx.lineTo(18, 62); ctx.moveTo(9, 66); ctx.lineTo(18, 66); ctx.moveTo(9, 70); ctx.lineTo(15, 70); ctx.stroke();
      ctx.fillStyle = "#CBD5E1"; ctx.beginPath(); ctx.roundRect(10, 49, 8, 5, 1.5); ctx.fill();
      ctx.fillStyle = body; ctx.beginPath(); ctx.ellipse(22, 70, 6, 5.5, 0, 0, 7); ctx.fill();
    }
  }

  function render(ci, p, expr, blink) {
    const c = document.createElement("canvas");
    c.width = c.height = PX;
    const ctx = c.getContext("2d");
    ctx.scale(PX / BASE, PX / BASE);
    const body = ci < 0 ? UNDERSTUDY : PALETTE[ci % PALETTE.length]; // ci < 0: an understudy who has not spoken yet
    // soft coloured contact shadow
    const sh = ctx.createRadialGradient(48, 91, 2, 48, 91, 32);
    sh.addColorStop(0, hexA(body, 0.55)); sh.addColorStop(1, hexA(body, 0));
    ctx.fillStyle = sh; ctx.beginPath(); ctx.ellipse(48, 90, 34, 7, 0, 0, 7); ctx.fill();
    // feet
    ctx.fillStyle = shade(body, -0.32);
    ctx.beginPath(); ctx.ellipse(36, 87, 9, 5, 0, 0, 7); ctx.fill();
    ctx.beginPath(); ctx.ellipse(60, 87, 9, 5, 0, 0, 7); ctx.fill();
    // glossy body
    blob(ctx, 48, 55, 34, ci * 13 + 3);
    const g = ctx.createRadialGradient(36, 34, 3, 52, 60, 48);
    g.addColorStop(0, shade(body, 0.32)); g.addColorStop(0.42, body); g.addColorStop(1, shade(body, -0.34));
    ctx.fillStyle = g; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = shade(body, -0.42); ctx.stroke();
    ctx.save(); blob(ctx, 48, 55, 34, ci * 13 + 3); ctx.clip();
    const rim = ctx.createLinearGradient(0, 70, 0, 92); rim.addColorStop(0, "rgba(0,0,0,0)"); rim.addColorStop(1, "rgba(10,5,30,.28)");
    ctx.fillStyle = rim; ctx.fillRect(0, 60, 96, 36);
    ctx.restore();
    // specular highlight
    ctx.fillStyle = "rgba(255,255,255,.78)"; ctx.beginPath(); ctx.ellipse(33, 32, 9, 5.5, -0.6, 0, 7); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.beginPath(); ctx.arc(45, 25, 2.2, 0, 7); ctx.fill();
    if (expr === "calm" || expr === "sleep") {
      ctx.fillStyle = "rgba(255,61,113,.35)"; ctx.beginPath(); ctx.ellipse(28, 60, 6, 4, 0, 0, 7); ctx.ellipse(68, 60, 6, 4, 0, 0, 7); ctx.fill();
    }
    if (p === "apron") prop(ctx, p, body);
    eyes(ctx, expr, blink);
    mouth(ctx, expr);
    if (expr === "worried") drop(ctx, 80, 34, 1);
    if (expr === "panic") { drop(ctx, 82, 30, 1.1); drop(ctx, 14, 40, 0.8); }
    if (p !== "apron" && p !== "none") prop(ctx, p, body);
    return c;
  }

  function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

  const glows = new Map();
  function glow(color) {
    let g = glows.get(color);
    if (g) return g;
    g = document.createElement("canvas"); g.width = g.height = 128;
    const x = g.getContext("2d"), r = x.createRadialGradient(64, 64, 6, 64, 64, 64);
    r.addColorStop(0, hexA(color, 0.9)); r.addColorStop(0.35, hexA(color, 0.45)); r.addColorStop(1, hexA(color, 0));
    x.fillStyle = r; x.fillRect(0, 0, 128, 128);
    glows.set(color, g);
    return g;
  }

  function sprite(ci, p, expr, blink) {
    const key = `${ci}|${p}|${expr}|${blink ? 1 : 0}`;
    let s = cache.get(key);
    if (!s) { s = render(ci, p, expr, blink); cache.set(key, s); }
    return s;
  }

  function exprFor(sev) {
    if (!sev) return "calm";
    if (sev <= 2) return "uneasy";
    if (sev === 3) return "worried";
    return "panic";
  }

  window.Creatures = { sprite, glow, propFor, exprFor, PALETTE, INK };
})();
