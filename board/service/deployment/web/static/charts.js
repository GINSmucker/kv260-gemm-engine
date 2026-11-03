import { escape as e } from './api-client.js';

const n = (value, digits=1) => Number.isFinite(value) ? value.toFixed(digits) : '—';
export function statistics(values) {
  if (!values.length || values.some(v => !Number.isFinite(v) || v < 0)) return null;
  const sorted=[...values].sort((a,b)=>a-b);
  const percentile=p=>{const x=(sorted.length-1)*p,i=Math.floor(x);return sorted[i]+(sorted[Math.ceil(x)]-sorted[i])*(x-i);};
  return { count:sorted.length,min:sorted[0],max:sorted.at(-1),p50:percentile(.5),p95:percentile(.95) };
}
export function comparison(samples, reference='w8') {
  const field=reference==='fp32'?'fp32Ms':'w8a8Ms';
  const cpu=statistics(samples.map(s=>s[field])),pl=statistics(samples.map(s=>s.plMs));
  if(!cpu||!pl)return null;
  return {field,cpu,pl,ratio:pl.p50>0?cpu.p50/pl.p50:null,saved:cpu.p50-pl.p50,reduction:cpu.p50>0?(cpu.p50-pl.p50)/cpu.p50*100:null};
}
const referenceName=reference=>reference==='fp32'?'ARM FP32':'ARM W8A8 参考';
export function referenceControl(reference='w8') {
  return `<div class="chart-switch" role="group" aria-label="曲线参考路径">${[['w8','W8A8 语义'],['fp32','FP32']].map(([key,label])=>`<button type="button" data-action="benchmark-reference" data-focus="reference-${key}" data-reference="${key}" aria-pressed="${reference===key}">${label}</button>`).join('')}</div>`;
}
export function comparisonChart(samples,reference='w8',id='comparison') {
  const c=comparison(samples,reference);
  if(!c)return '<div class="visual-empty">尚无完整的 CPU / PS·PL 配对数据，曲线将在实际结果返回后显示。</div>';
  const W=880,H=304,L=65,R=27,T=28,B=48,maximum=Math.max(c.cpu.max,c.pl.max);
  const step=maximum>0?10**Math.floor(Math.log10(maximum))/2:1;
  const ceiling=Math.max(step,Math.ceil(maximum*1.15/step)*step);
  const x=i=>L+(W-L-R)*(samples.length===1?.5:i/(samples.length-1)),y=v=>H-B-v/ceiling*(H-T-B);
  const points=field=>samples.map((s,i)=>`${x(i)},${y(s[field])}`).join(' ');
  const ticks=Array.from({length:5},(_,i)=>i*ceiling/4);
  const cpuName=referenceName(reference),label=`${cpuName} 与 PS/PL 实际逐次耗时，${samples.length} 个样本，单位毫秒`;
  return `<div class="curve-shell"><div class="chart-legend measured-legend"><span><i class="legend-cpu"></i>${cpuName}</span><span><i class="legend-pl"></i>PS/PL</span><small>模型调用 · ms</small></div><svg class="measured-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${e(label)}"><title>${e(label)}</title><defs><linearGradient id="${id}-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2c9a83" stop-opacity=".16"/><stop offset="100%" stop-color="#2c9a83" stop-opacity=".015"/></linearGradient></defs>${ticks.map(v=>`<g class="plot-grid"><path d="M${L},${y(v)}H${W-R}"/><text x="${L-13}" y="${y(v)+4}" text-anchor="end">${n(v,0)}</text></g>`).join('')}<text class="plot-axis" x="${L}" y="15">耗时 / ms</text><polygon points="${L},${H-B} ${points('plMs')} ${W-R},${H-B}" fill="url(#${id}-fill)"/><polyline class="curve-cpu" points="${points(c.field)}"/><polyline class="curve-pl" points="${points('plMs')}"/>${samples.map((s,i)=>{const left=Math.min(Math.max(x(i)-94,L),W-R-190),top=Math.max(T,Math.min(y(s[c.field]),y(s.plMs))-65);return `<g class="curve-pair" tabindex="0" aria-label="样本 ${e(s.index)}，${cpuName} ${n(s[c.field],3)} 毫秒，PS/PL ${n(s.plMs,3)} 毫秒"><line x1="${x(i)}" x2="${x(i)}" y1="${T}" y2="${H-B}" class="plot-guide"/><circle cx="${x(i)}" cy="${y(s[c.field])}" r="4.5" class="dot-cpu"/><circle cx="${x(i)}" cy="${y(s.plMs)}" r="5" class="dot-pl"/><circle cx="${x(i)}" cy="${y(s.plMs)}" r="14" fill="transparent"/><g class="plot-tooltip"><rect x="${left}" y="${top}" width="190" height="53" rx="8"/><text x="${left+11}" y="${top+18}">样本 ${e(s.index)} · ${cpuName}</text><text x="${left+11}" y="${top+37}">${n(s[c.field],3)} ms  /  PL ${n(s.plMs,3)} ms</text></g></g>`;}).join('')}${samples.filter((s,i)=>samples.length<=20||i%20===0||i===samples.length-1).map((s)=>{const i=samples.indexOf(s);return `<text class="plot-tick" x="${x(i)}" y="${H-B+25}" text-anchor="middle">${e(s.index)}</text>`;}).join('')}<text class="plot-axis" x="${W-R}" y="${H-5}" text-anchor="end">图片序号</text></svg><div class="chart-caption"><span>● 悬停或键盘聚焦数据点查看原始值</span><span>全部 ${samples.length} 张配对图片 · 保留全部慢点</span></div></div>`;
}
export function distribution(samples,reference='w8') {
  const c=comparison(samples,reference);if(!c)return '';
  const max=Math.max(c.cpu.max,c.pl.max,1)*1.08;
  return `<div class="distribution">${[[referenceName(reference),c.cpu,'cpu'],['PS/PL',c.pl,'pl']].map(([label,s,key])=>`<div class="distribution-row"><div><strong>${label}</strong><span>${s.count} 个实际样本</span></div><div class="range-track ${key}" aria-label="${label}，最小 ${n(s.min,3)}，中位 ${n(s.p50,3)}，最大 ${n(s.max,3)} 毫秒"><i style="left:${s.min/max*100}%;width:${(s.max-s.min)/max*100}%"></i><b style="left:${s.p50/max*100}%"></b></div><div class="range-values"><span>min <b>${n(s.min)}</b></span><span>p50 <b>${n(s.p50)}</b></span><span>p95 <b>${n(s.p95)}</b></span><span>max <b>${n(s.max)}</b></span></div></div>`).join('')}</div>`;
}
export function performanceMetrics(samples,reference='w8') {
  const c=comparison(samples,reference);if(!c)return '';
  return `<div class="performance-metrics"><div class="performance-primary"><label>CPU / PS·PL 的 p50 比值</label><strong>${n(c.ratio,2)}<small>×</small></strong><span>${referenceName(reference)} 作为对照</span></div><div><label>PS/PL p50</label><strong>${n(c.pl.p50,2)}<small>ms</small></strong><span>p95 ${n(c.pl.p95,2)} ms</span></div><div><label>p50 耗时变化</label><strong class="${c.saved<0?'metric-regression':''}">${c.saved>=0?'−':'+'}${n(Math.abs(c.saved),2)}<small>ms</small></strong><span>${c.reduction===null?'比例未报告':`${c.reduction>=0?'减少':'增加'} ${n(Math.abs(c.reduction),1)}%`}</span></div><div><label>完整配对样本</label><strong>${c.pl.count}<small>次</small></strong><span>保留全部样本及慢点</span></div></div>`;
}
