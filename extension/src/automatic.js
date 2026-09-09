// Scheduling is separate from business/identity verification. A collected but
// unverified snapshot stops repeated requests without becoming verified data.
export const AUTO_DEFAULTS = Object.freeze({ enabled: true, cooldownMinutes: 30, maxAttemptsPerDay: 3 });
export function localDay(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
export function autoPolicy(value = {}) {
  const bounded = (v, fallback, min, max) => Number.isFinite(Number(v)) ? Math.min(max, Math.max(min, Number(v))) : fallback;
  return { enabled: value.enabled !== false,
    cooldownMinutes: bounded(value.cooldownMinutes, 30, 1, 1440),
    maxAttemptsPerDay: Math.floor(bounded(value.maxAttemptsPerDay, 3, 1, 10)) };
}
export function automaticDecision(record, policy, now = new Date()) {
  if (!policy.enabled) return "disabled";
  if (!record || record.day !== localDay(now)) return "run";
  if (record.collected) return "collected_today";
  if (record.attempts >= policy.maxAttemptsPerDay) return "daily_limit";
  if (now.getTime() - record.attemptedAt < policy.cooldownMinutes * 60000) return "retry_cooldown";
  return "run";
}
export function snapshotComplete(result) {
  return result?.collector?.current_page_unchanged === true &&
    Array.isArray(result.collector.failed_modules) && result.collector.failed_modules.length === 0;
}
