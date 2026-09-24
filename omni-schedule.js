const OMNI_SLOT_MS = 15 * 60 * 1000;

function nextOmniCheckAt(fromMs) {
  const t = Number(fromMs);
  const now = Number.isFinite(t) ? t : Date.now();
  const step = OMNI_SLOT_MS;
  let when = Math.floor(now / step) * step + step;
  if (when <= now) when += step;
  return when;
}

function omniAckActive(untilMs, fromMs) {
  const until = Number(untilMs) || 0;
  const now = Number.isFinite(Number(fromMs)) ? Number(fromMs) : Date.now();
  return until > now;
}
