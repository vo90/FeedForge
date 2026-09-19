export async function resolveStemPreference(enabled, options, checkServer) {
  if (!enabled) return { options: { ...options, separateStems: false } };
  let status;
  try { status = await checkServer(); }
  catch (error) { status = { ready: false, error: error.message }; }
  return status?.ready
    ? { options: { ...options, separateStems: true } }
    : { needsDecision: true, error: status?.error || "The stem server is not ready." };
}
