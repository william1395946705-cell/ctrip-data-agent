import { test } from 'node:test';
import assert from 'node:assert/strict';
import { COLUMNS, tableRows, toCsv } from '../src/table.js';

const sample = () => ({ hotel: { hotel_name: 'Synthetic Hotel', hotel_id: '000123' },
  collected_at: '2026-09-07T08:17:35.171Z',
  collector: { trigger: 'automatic', current_page_unchanged: true, failed_modules: [], warnings: [], hotel_identity_verification: 'manual_check_required',
    statistics_window: { operating_date: '2026-09-06', roas_start: '2026-08-31', roas_end: '2026-09-06', timezone: 'Asia/Shanghai' } },
  operating_report: { room_night_rank: '10 / 14', hotel_order_conversion: 0, comp_order_conversion: 0.2, psi_score: null }, pyramid: { roas_7d: 3.2 }, violation: { status: '无违约' }
});

test('table preserves dates, percentage zero, missing values and identity warning', () => {
  const row = tableRows([sample()])[0];
  assert.equal(row.length, COLUMNS.length);
  assert.equal(row[2], '2026-09-07 16:17:35');
  assert.equal(row[4], '2026-09-06');
  assert.equal(row[9], '数据已采集，门店待复核');
  assert.equal(row[14], '');
  assert.equal(row[19], '0.00%');
  assert.equal(row[20], '20.00%');
  const failed = sample(); failed.collector.failed_modules = ['pyramid'];
  assert.equal(tableRows([failed])[0][9], '采集异常');
});

test('CSV has BOM and escapes quotes, linebreaks, formulas, ID and ranking', () => {
  const value = sample(); value.hotel.hotel_name = '=HYPERLINK("bad")\n,hotel';
  const csv = toCsv([value]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.ok(csv.includes('"\'=HYPERLINK(""bad"")\n,hotel"'));
  assert.ok(csv.includes('"\'000123"'));
  assert.ok(csv.includes('"\'10 / 14"'));
  value.collector.warnings = ['Bearer abcdefghijklmnopqrstuvwxyz'];
  assert.ok(!toCsv([value]).includes('abcdefghijklmnopqrstuvwxyz'));
  assert.equal(toCsv([]).split('\r\n').length, 1);
});
