const OMNI_SLOT_MS = 10 * 60 * 1000;

function nextOmniCheckAt(fromMs) {
  const t = Number(fromMs);
  const nowMs = Number.isFinite(t) ? t : Date.now();
  const now = new Date(nowMs);
  const next = new Date(nowMs);
  next.setSeconds(0, 0);
  const minute = now.getMinutes();
  const bump = minute % 10 === 0 ? 10 : 10 - (minute % 10);
  next.setMinutes(minute + bump);
  if (next.getTime() <= nowMs) next.setMinutes(next.getMinutes() + 10);
  return next.getTime();
}

function omniAckActive(untilMs, fromMs) {
  const until = Number(untilMs) || 0;
  const now = Number.isFinite(Number(fromMs)) ? Number(fromMs) : Date.now();
  return until > now;
}
