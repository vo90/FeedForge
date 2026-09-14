export const AUTO_SETTING = "auto";
export const GLOBAL_WORKER_CEILING = 32;

export const FALLBACK_PERFORMANCE_PROFILE = Object.freeze({
  logicalProcessors: 4,
  memoryGb: 8,
  recommendedWorkers: 2,
  manualMaxWorkers: 4
});

function positiveInteger(value, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.max(1, Math.floor(parsed));
}

export function normalizePerformanceProfile(value) {
  const profile = value && typeof value === "object" ? value : {};
  const logicalProcessors = positiveInteger(
    profile.logicalProcessors,
    FALLBACK_PERFORMANCE_PROFILE.logicalProcessors
  );
  const memoryGb = positiveInteger(profile.memoryGb, FALLBACK_PERFORMANCE_PROFILE.memoryGb);
  const derivedRecommended = Math.max(1, Math.min(
    24,
    Math.floor(logicalProcessors / 2),
    Math.floor(memoryGb / 2.5)
  ));
  const recommendedWorkers = Math.min(
    GLOBAL_WORKER_CEILING,
    positiveInteger(profile.recommendedWorkers, derivedRecommended)
  );
  const derivedMaximum = Math.max(recommendedWorkers, Math.min(
    GLOBAL_WORKER_CEILING,
    logicalProcessors,
    Math.floor(memoryGb / 2)
  ));
  const manualMaxWorkers = Math.max(recommendedWorkers, Math.min(
    GLOBAL_WORKER_CEILING,
    positiveInteger(profile.manualMaxWorkers, derivedMaximum)
  ));
  return { logicalProcessors, memoryGb, recommendedWorkers, manualMaxWorkers };
}

export function normalizeWorkerSetting(value, fallback = AUTO_SETTING) {
  if (value === AUTO_SETTING || value === undefined || value === null || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.floor(parsed));
}

export function clampWorkerSetting(value, performanceProfile) {
  const normalized = normalizeWorkerSetting(value);
  if (normalized === AUTO_SETTING) return AUTO_SETTING;
  const profile = normalizePerformanceProfile(performanceProfile);
  return Math.min(normalized, profile.manualMaxWorkers);
}

export function resolveSelectedWorkerCount(setting, performanceProfile) {
  const profile = normalizePerformanceProfile(performanceProfile);
  const normalized = clampWorkerSetting(setting, profile);
  return normalized === AUTO_SETTING ? profile.recommendedWorkers : normalized;
}

export function resolveConversionWorkerCount(selectedWorkers, options = {}) {
  const selected = Math.max(1, Math.floor(Number(selectedWorkers) || 1));
  if (!options.separateStems) return selected;
  const stemJobs = Math.max(1, Math.floor(Number(options.stemJobs) || 1));
  return Math.max(1, Math.min(selected, stemJobs + 1, 4));
}

/**
 * Bound workers inside one multi-song PSARC. Audio jobs share the parsed
 * archive, but validation and temporary PCM files still contend for CPU,
 * memory, and disk. The one-quarter-CPU rule measured six as the knee on a
 * 24-thread/32-GB machine while allowing stronger hosts to scale higher.
 */
export function resolveMultiSongWorkerCount(
  selectedWorkers,
  songCount,
  performanceProfile,
  options = {}
) {
  const selected = Math.max(1, Math.floor(Number(selectedWorkers) || 1));
  const songs = Math.max(1, Math.floor(Number(songCount) || 1));
  if (songs <= 1 || options.separateStems) return 1;
  const profile = normalizePerformanceProfile(performanceProfile);
  const cpuLimit = Math.max(1, Math.floor(profile.logicalProcessors / 4));
  const memoryLimit = Math.max(1, Math.floor(profile.memoryGb / 4));
  const machineLimit = Math.min(16, cpuLimit, memoryLimit);
  return Math.max(1, Math.min(selected, songs, machineLimit));
}

export function resolveInspectionWorkerCount(selectedWorkers) {
  const selected = Math.max(1, Math.floor(Number(selectedWorkers) || 1));
  return Math.max(1, Math.min(8, Math.ceil(selected / 2)));
}

export function manualWorkerOptions(performanceProfile) {
  const maximum = normalizePerformanceProfile(performanceProfile).manualMaxWorkers;
  return Array.from({ length: maximum }, (_unused, index) => index + 1);
}
