/* 极简 SVG 图表库：donut / bars / line — 零依赖，离线可用 */
"use strict";

const PALETTE = ["#4f8cff", "#34c98f", "#ffb020", "#ff6b6b", "#a06bff", "#22c3d6", "#f270a4", "#8fd460"];

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

/* 环形图 items: [{label, value}] */
function donut(el, items) {
  el.innerHTML = "";
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  const size = 180, r = 70, cx = size / 2, cy = size / 2;
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size });
  let angle = -90;
  items.forEach((it, i) => {
    const frac = it.value / total, a1 = angle, a2 = angle + frac * 360;
    const x1 = cx + r * Math.cos((a1 * Math.PI) / 180), y1 = cy + r * Math.sin((a1 * Math.PI) / 180);
    const x2 = cx + r * Math.cos((a2 * Math.PI) / 180), y2 = cy + r * Math.sin((a2 * Math.PI) / 180);
    const large = frac > 0.5 ? 1 : 0;
    const d = `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
    svg.appendChild(svgEl("path", { d, fill: PALETTE[i % PALETTE.length] }));
    angle = a2;
  });
  svg.appendChild(svgEl("text", { x: cx, y: cy - 4, "text-anchor": "middle", class: "donut-total" }));
  svg.lastChild.textContent = total;
  svg.appendChild(svgEl("text", { x: cx, y: cy + 16, "text-anchor": "middle", class: "donut-label" }));
  svg.lastChild.textContent = "总计";
  el.appendChild(svg);
  const legend = document.createElement("div");
  legend.className = "legend";
  items.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "legend-row";
    row.innerHTML = `<span class="dot" style="background:${PALETTE[i % PALETTE.length]}"></span>${it.label}<b>${it.value.toLocaleString()}</b>`;
    legend.appendChild(row);
  });
  el.appendChild(legend);
}

/* 横向条形图 items: [{label, value}] */
function bars(el, items, color = "#4f8cff") {
  el.innerHTML = "";
  const max = Math.max(...items.map(i => i.value), 1);
  items.forEach((it, i) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    const pct = (it.value / max) * 100;
    row.innerHTML = `
      <span class="bar-label">${it.label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${color}"></span></span>
      <span class="bar-value">${it.value.toLocaleString()}</span>`;
    el.appendChild(row);
  });
}

/* 折线图 series: [{name, values[], color?}] */
function line(el, labels, series) {
  el.innerHTML = "";
  const W = 720, H = 220, pad = { l: 36, r: 12, t: 16, b: 24 };
  const max = Math.max(...series.flatMap(s => s.values), 1);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", preserveAspectRatio: "xMidYMid meet" });
  const ix = i => pad.l + (i * (W - pad.l - pad.r)) / Math.max(labels.length - 1, 1);
  const iy = v => H - pad.b - (v / max) * (H - pad.t - pad.b);
  // 网格
  for (let g = 0; g <= 4; g++) {
    const y = pad.t + (g * (H - pad.t - pad.b)) / 4;
    svg.appendChild(svgEl("line", { x1: pad.l, y1: y, x2: W - pad.r, y2: y, class: "grid" }));
    svg.appendChild(svgEl("text", { x: 4, y: y + 4, class: "axis" })).textContent = Math.round(max * (1 - g / 4));
  }
  series.forEach((s, si) => {
    const col = s.color || PALETTE[si % PALETTE.length];
    let d = "";
    s.values.forEach((v, i) => {
      d += `${i ? "L" : "M"} ${ix(i)} ${iy(v)} `;
    });
    svg.appendChild(svgEl("path", { d, fill: "none", stroke: col, "stroke-width": 2 }));
    s.values.forEach((v, i) => {
      svg.appendChild(svgEl("circle", { cx: ix(i), cy: iy(v), r: 2.5, fill: col }));
    });
  });
  // X 轴标签（稀疏显示）
  const step = Math.ceil(labels.length / 8);
  labels.forEach((lb, i) => {
    if (i % step === 0 || i === labels.length - 1) {
      svg.appendChild(svgEl("text", { x: ix(i), y: H - 6, "text-anchor": "middle", class: "axis" })).textContent = lb;
    }
  });
  el.appendChild(svg);
  const legend = document.createElement("div");
  legend.className = "legend inline";
  series.forEach((s, si) => {
    const row = document.createElement("span");
    row.innerHTML = `<span class="dot" style="background:${s.color || PALETTE[si % PALETTE.length]}"></span>${s.name}`;
    legend.appendChild(row);
  });
  el.appendChild(legend);
}
