export const SAFE_VALIDATION_POLICY = "safe";
export const STRICT_VALIDATION_POLICY = "strict";

export function normalizeValidationPolicy(value) {
  return String(value || "").trim().toLowerCase() === STRICT_VALIDATION_POLICY
    ? STRICT_VALIDATION_POLICY
    : SAFE_VALIDATION_POLICY;
}

function warningMessage(value) {
  if (typeof value === "string") return value.trim();
  if (value && typeof value === "object") {
    return String(value.message || value.error || value.detail || "").trim();
  }
  return "";
}

export function normalizeWarnings(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(warningMessage).filter(Boolean))];
}

function nonNegativeInteger(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number) && number >= 0) return Math.floor(number);
  }
  return 0;
}

export function conversionOutcome(result = {}) {
  const validation = result?.validation && typeof result.validation === "object"
    ? result.validation
    : {};
  const warnings = normalizeWarnings(result?.warnings);
  let chartWarnings = normalizeWarnings([
    ...(Array.isArray(result?.chartWarnings) ? result.chartWarnings : []),
    ...(Array.isArray(result?.chart_warnings) ? result.chart_warnings : []),
    ...(Array.isArray(validation?.chartWarnings) ? validation.chartWarnings : []),
    ...(Array.isArray(validation?.chart_warnings) ? validation.chart_warnings : [])
  ]);
  const explicitFlag = result?.convertedWithChartWarnings === true
    || result?.converted_with_chart_warnings === true
    || validation?.convertedWithChartWarnings === true
    || validation?.converted_with_chart_warnings === true;
  let chartWarningCount = nonNegativeInteger(
    result?.chartWarningCount,
    result?.chart_warning_count,
    validation?.chartWarningCount,
    validation?.chart_warning_count
  );

  chartWarningCount = Math.max(chartWarningCount, chartWarnings.length, explicitFlag ? 1 : 0);

  return {
    warnings,
    chartWarnings,
    chartWarningCount,
    convertedWithChartWarnings: explicitFlag || chartWarningCount > 0
  };
}

export function isConversionIssue(item = {}) {
  return ["failed", "partial", "needs-review"].includes(item?.status);
}

export function conversionStatusText(status) {
  return {
    queued: "Queued",
    inspecting: "Inspecting",
    ready: "Ready",
    "needs-review": "Review",
    converting: "Converting",
    converted: "Converted",
    partial: "Partially converted",
    failed: "Failed"
  }[status] || "Waiting";
}

function outputPathValue(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function normalizeOutputResults(result = {}) {
  const candidates = [
    result?.outputResults,
    result?.conversionResults,
    result?.results
  ];
  const rows = candidates.find(Array.isArray) || [];
  return rows.flatMap((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row)) return [];
    const outputPath = outputPathValue(row.outputPath || row.output_path);
    if (!outputPath) return [];
    return [{ ...row, outputPath }];
  });
}

export function conversionOutputPaths(result = {}, outputResults = normalizeOutputResults(result)) {
  const paths = [
    ...(Array.isArray(result?.outputPaths) ? result.outputPaths : []),
    result?.outputPath,
    ...outputResults.map((row) => row.outputPath)
  ].map(outputPathValue).filter(Boolean);
  const seen = new Set();
  return paths.filter((path) => {
    const key = path.replaceAll("/", "\\").toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function conversionResultStatus(result = {}, outputPaths = conversionOutputPaths(result)) {
  if (result?.ok === true) return "converted";
  if (result?.partial === true && outputPaths.length > 0) return "partial";
  return "failed";
}

function outputParent(path) {
  const value = outputPathValue(path).replace(/[\\/]+$/, "");
  const separator = Math.max(value.lastIndexOf("\\"), value.lastIndexOf("/"));
  return separator > 0 ? value.slice(0, separator) : "";
}

export function outputLocationSummary(outputPaths = []) {
  const folders = [];
  const seen = new Set();
  for (const path of Array.isArray(outputPaths) ? outputPaths : []) {
    const folder = outputParent(path);
    if (!folder) continue;
    const key = folder.replaceAll("/", "\\").toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    folders.push(folder);
  }
  if (folders.length === 1) {
    return { folders, folderCount: 1, suffix: ` in ${folders[0]}` };
  }
  if (folders.length > 1) {
    return { folders, folderCount: folders.length, suffix: ` across ${folders.length} folders` };
  }
  return { folders: [], folderCount: 0, suffix: "" };
}
