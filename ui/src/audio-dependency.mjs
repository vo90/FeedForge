const FALLBACK_MESSAGE = "FeedForge could not verify its WEM audio decoder. PSARC conversion was not started, so no WEM-only FeedPaks were created.";

export function isPsarcPath(value) {
  return String(value || "").trim().toLowerCase().endsWith(".psarc");
}

export function queueRequiresAudioDecoder(items) {
  return (items || []).some((item) => isPsarcPath(item?.path || item));
}

export function normalizeAudioDecoderStatus(value) {
  const status = value && typeof value === "object" ? value : {};
  return {
    ready: status.ready === true,
    available: status.available === true,
    version: String(status.version || ""),
    source: String(status.source || ""),
    message: String(status.message || (status.ready === true ? "WEM audio decoder is ready." : FALLBACK_MESSAGE)),
    error: String(status.error || "")
  };
}

export async function checkAudioDecoder(api, options = { refresh: true }) {
  if (typeof api?.getAudioDecoderStatus !== "function") {
    return normalizeAudioDecoderStatus({
      ready: false,
      error: "Audio decoder status is unavailable.",
      message: FALLBACK_MESSAGE
    });
  }
  try {
    return normalizeAudioDecoderStatus(await api.getAudioDecoderStatus(options));
  } catch (error) {
    return normalizeAudioDecoderStatus({
      ready: false,
      error: error?.message || "Audio decoder check failed.",
      message: FALLBACK_MESSAGE
    });
  }
}

export { FALLBACK_MESSAGE };
