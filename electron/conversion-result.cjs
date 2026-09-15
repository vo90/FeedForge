"use strict";

function uniqueStrings(values) {
  return [...new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean))];
}

function normalizeConversionDetails(values) {
  const normalized = [];
  const seen = new Set();
  for (const value of values || []) {
    const source = value && typeof value === "object" && !Array.isArray(value)
      ? value
      : { message: value };
    const message = String(source.message || "").trim();
    if (!message) continue;
    const category = String(source.category || "technical").trim() || "technical";
    const key = `${category}\u0000${message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    normalized.push({ message, category });
  }
  return normalized;
}

function sanitizeConversionResult(value) {
  const entry = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const chartWarnings = uniqueStrings(Array.isArray(entry.chartWarnings) ? entry.chartWarnings : []);
  const chartWarningCount = Math.max(0, Number(entry.chartWarningCount) || 0);
  const conversionDetails = normalizeConversionDetails([
    ...(Array.isArray(entry.conversionDetails) ? entry.conversionDetails : []),
    ...(Array.isArray(entry.conversion_details) ? entry.conversion_details : []),
    ...(Array.isArray(entry.details) ? entry.details : [])
  ]);
  return {
    outputPath: String(entry.outputPath || "").trim(),
    validationOk: entry.validationOk === true ? true : entry.validationOk === false ? false : null,
    publishable: entry.publishable !== false,
    convertedWithChartWarnings: entry.convertedWithChartWarnings === true
      || chartWarningCount > 0
      || chartWarnings.length > 0,
    chartWarningCount,
    chartWarnings,
    warnings: uniqueStrings(Array.isArray(entry.warnings) ? entry.warnings : []),
    conversionDetails
  };
}

function qualifyConversionDetail(outputPath, detail, includeOutputPath) {
  const message = String(detail || "").trim();
  if (!message || !includeOutputPath) return message;
  const outputLabel = String(outputPath || "").trim();
  return outputLabel ? `${outputLabel}: ${message}` : message;
}

function aggregateConversionDetails(entries, includeOutputPath) {
  return normalizeConversionDetails((entries || []).flatMap((entry) => (
    normalizeConversionDetails(entry?.conversionDetails).map((detail) => ({
      ...detail,
      message: qualifyConversionDetail(entry?.outputPath, detail.message, includeOutputPath)
    }))
  )));
}

function filterFallbackWarnings(values, entries) {
  const structuredWarningSet = new Set(
    (entries || []).flatMap((entry) => uniqueStrings(entry?.warnings))
  );
  const structuredDetailSet = new Set(
    (entries || []).flatMap((entry) => (
      normalizeConversionDetails(entry?.conversionDetails).map((detail) => detail.message)
    ))
  );
  return uniqueStrings(values).filter(
    (message) => !structuredWarningSet.has(message) && !structuredDetailSet.has(message)
  );
}

module.exports = {
  aggregateConversionDetails,
  filterFallbackWarnings,
  normalizeConversionDetails,
  qualifyConversionDetail,
  sanitizeConversionResult,
  uniqueStrings
};
