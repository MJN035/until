const HEALTH_URL = "https://until-app.onrender.com/healthz";
const HEALTH_TIMEOUT_MS = 8_000;

async function keepWarm() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);

  try {
    const response = await fetch(HEALTH_URL, {
      method: "GET",
      headers: { "User-Agent": "until-landing-keep-warm/1.0" },
      signal: controller.signal,
    });
    // The health body is intentionally neither read nor retained.
    if (response.body) await response.body.cancel();
  } catch (_) {
    // A keep-warm failure must never affect static landing-page delivery.
  } finally {
    clearTimeout(timeout);
  }
}

export default {
  fetch(request, env) {
    return env.ASSETS.fetch(request);
  },

  scheduled(_controller, _env, ctx) {
    ctx.waitUntil(keepWarm());
  },
};
