import { sanitizeForStorage } from './core.js';

export const COLUMNS = [
  ['酒店名称', 'hotel.hotel_name'], ['酒店ID（接口返回）', 'hotel.hotel_id'],
  ['采集时间（北京时间）', 'collected_at', 'time'], ['采集时间（UTC）', 'collected_at'],
  ['经营数据日期', 'collector.statistics_window.operating_date'],
  ['ROAS开始日期', 'collector.statistics_window.roas_start'], ['ROAS结束日期', 'collector.statistics_window.roas_end'],
  ['请求日期所用时区', 'collector.statistics_window.timezone'],
  ['采集方式', 'collector.trigger'], ['结果状态', '', 'status'],
  ['经营提醒', 'operating_report.operating_reminder'], ['昨日离店间夜', 'operating_report.departed_room_nights'],
  ['竞争圈排名', 'operating_report.room_night_rank'], ['点评分', 'operating_report.review_score'], ['PSI', 'operating_report.psi_score'],
  ['本店曝光', 'operating_report.hotel_list_exposure'], ['竞争圈曝光', 'operating_report.comp_list_exposure'],
  ['本店曝光转化率', 'operating_report.hotel_exposure_conversion', 'percent'], ['竞争圈曝光转化率', 'operating_report.comp_exposure_conversion', 'percent'],
  ['本店下单转化率', 'operating_report.hotel_order_conversion', 'percent'], ['竞争圈下单转化率', 'operating_report.comp_order_conversion', 'percent'],
  ['四象限', 'operating_report.category'], ['近7天ROAS', 'pyramid.roas_7d'], ['违约状态', 'violation.status'],
  ['页面未改变', 'collector.current_page_unchanged', 'boolean'], ['失败模块', 'collector.failed_modules'], ['提示', 'collector.warnings']
];
export function tableRows(records) {
  return records.map(raw => {
    const record = sanitizeForStorage(raw);
    return COLUMNS.map(([, path, format]) => {
      let value = path.split('.').reduce((obj, key) => obj?.[key], record);
      if (format === 'status') {
        const c = record.collector;
        if (!c || !Array.isArray(c.failed_modules)) return '状态未知';
        if (c.failed_modules.length || c.current_page_unchanged !== true) return '采集异常';
        return c.hotel_identity_verification === 'page_id_bound' ? '采集完成' : '数据已采集，门店待复核';
      }
      if (value === null || value === undefined) return '';
      if (format === 'time') return Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('sv-SE', {
        timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
      }).format(new Date(value)) : '';
      if (format === 'percent') return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '';
      if (format === 'boolean') return value === true ? '是' : '否';
      if (Array.isArray(value)) return value.join('；');
      return typeof value === 'object' ? '' : value;
    });
  });
}
export function toCsv(records) {
  // Quote delimiters/newlines and neutralize spreadsheet formula injection.
  const cell = value => {
    let text = String(value ?? '');
    if (/^[\s\uFEFF]*[=+@-]/u.test(text) || /^[\t\r\n]/u.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const rows = tableRows(records).map(row => row.map((value, index) =>
    value !== '' && [1, 12].includes(index) ? "'" + value : value));
  return '\uFEFF' + [COLUMNS.map(c => c[0]), ...rows].map(row => row.map(cell).join(',')).join('\r\n');
}
