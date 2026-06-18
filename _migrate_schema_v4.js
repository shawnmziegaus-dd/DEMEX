/* DEMEX schema v4 migration for on-disk JSON database/import files.
   Adds estimating columns WITHOUT dropping any existing field. Idempotent.
   Mirrors the normalization logic embedded in DEMEX.html (DX store). */
const fs = require('fs');

const CATS = ['labor', 'disposal', 'equipment', 'material', 'sub'];

function deriveScopeType(p) {
  const s = ((p.scope || '') + ' ' + (p.name || '') + ' ' + (p.notes || '') + ' ' + (p.type || '')).toLowerCase();
  const contain = /(abat|asbestos|mold|microbial|lead\b|remediat|containment|hazmat|encapsul|negative air|haz\b)/.test(s);
  const demo = /(demo|demolition|removal|selective|interior strip|gut|dismantle|disassemble|sitework|wreck)/.test(s);
  if (contain && demo) return 'both';
  if (contain) return 'containment';
  return 'demolition';
}
function mapEQ(arr) {
  return (Array.isArray(arr) ? arr : []).map(q => ({
    item: q.item || q.label || q.svc || '', svc: q.svc || '', unit: q.unit || '',
    qty: +q.qty || 0, location: q.location || '', notes: q.notes || ''
  }));
}
function blankActuals() { return { labor: 0, disposal: 0, equipment: 0, material: 0, sub: 0, total: 0, recordedAt: null, source: '' }; }
function rollupActuals(data) {
  const m = {};
  (data.actuals || []).forEach(a => {
    if (!a.project_id) return;
    const o = m[a.project_id] || (m[a.project_id] = blankActuals());
    if (CATS.indexOf(a.category) >= 0) o[a.category] += (+a.amount || 0);
    o.total += (+a.amount || 0);
    if (a.date && (!o.recordedAt || a.date > o.recordedAt)) o.recordedAt = a.date;
    o.source = 'rollup:actuals';
  });
  return m;
}
function normalizeProject(p, acc) {
  if (!p.scope_type) p.scope_type = deriveScopeType(p);
  if (!Array.isArray(p.estimated_quantities)) p.estimated_quantities = mapEQ(p.estQty);
  if (!Array.isArray(p.unit_rates)) p.unit_rates = [];
  if (!Array.isArray(p.bulk_pricing_thresholds)) p.bulk_pricing_thresholds = [];
  const roll = acc && acc[p.id];
  if (roll) {
    const keepAt = (p.actual_realized_costs || {}).recordedAt;
    p.actual_realized_costs = roll;
    if (!p.actual_realized_costs.recordedAt) p.actual_realized_costs.recordedAt = keepAt || null;
  } else if (!p.actual_realized_costs) {
    p.actual_realized_costs = blankActuals();
  }
  p.win_loss_status = p.status === 'won' ? 'won' : (p.status === 'lost' ? 'lost' : 'pending');
  return p;
}

const NEW_COLS = ['scope_type', 'estimated_quantities', 'unit_rates', 'bulk_pricing_thresholds', 'actual_realized_costs', 'win_loss_status'];

function migrateFile(path) {
  const before = fs.readFileSync(path, 'utf8');
  const data = JSON.parse(before);
  if (!Array.isArray(data.projects)) { console.log(`SKIP ${path} (no projects array)`); return; }
  const acc = rollupActuals(data);
  const counts = { demolition: 0, containment: 0, both: 0, won: 0, lost: 0, pending: 0, withActuals: 0 };
  data.projects.forEach(p => {
    normalizeProject(p, acc);
    counts[p.scope_type]++;
    counts[p.win_loss_status]++;
    if ((p.actual_realized_costs || {}).total > 0) counts.withActuals++;
  });
  data.v = 4;
  fs.writeFileSync(path, JSON.stringify(data, null, 2));
  const sample = data.projects[0] || {};
  console.log(`\n=== ${path} ===`);
  console.log(`projects: ${data.projects.length}  clients: ${(data.clients || []).length}`);
  console.log(`scope_type -> demolition:${counts.demolition} containment:${counts.containment} both:${counts.both}`);
  console.log(`win_loss   -> won:${counts.won} lost:${counts.lost} pending:${counts.pending}`);
  console.log(`projects with realized actuals rolled up: ${counts.withActuals}`);
  console.log(`new columns present on sample project: ${NEW_COLS.every(c => c in sample) ? 'ALL OK' : 'MISSING'}`);
}

['demex_full_history_import.json', 'demex_itb_import_2026-06-17.json', 'demex_itb_import_2026-06-18.json'].forEach(migrateFile);
console.log('\nMigration complete.');
