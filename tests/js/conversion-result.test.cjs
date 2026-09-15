"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  aggregateConversionDetails,
  filterFallbackWarnings,
  sanitizeConversionResult
} = require("../../electron/conversion-result.cjs");

test("conversion details default to an empty debug-only collection", () => {
  const result = sanitizeConversionResult({
    outputPath: "song.feedpak",
    warnings: ["A material conversion warning."]
  });

  assert.deepEqual(result.conversionDetails, []);
  assert.deepEqual(result.warnings, ["A material conversion warning."]);
});

test("conversion details are sanitized without entering user-facing warnings", () => {
  const result = sanitizeConversionResult({
    outputPath: " song.feedpak ",
    chartWarningCount: 2,
    chartWarnings: ["high fret preserved"],
    warnings: ["material warning"],
    conversionDetails: [
      { message: " bend normalized ", category: "source-normalization" },
      { message: "bend normalized", category: "source-normalization" },
      { message: "high fret preserved", category: "chart-validation" },
      { category: "ignored" }
    ]
  });

  assert.deepEqual(result.warnings, ["material warning"]);
  assert.deepEqual(result.chartWarnings, ["high fret preserved"]);
  assert.equal(result.convertedWithChartWarnings, true);
  assert.deepEqual(result.conversionDetails, [
    { message: "bend normalized", category: "source-normalization" },
    { message: "high fret preserved", category: "chart-validation" }
  ]);
});

test("legacy detail field names remain accepted", () => {
  const result = sanitizeConversionResult({
    conversion_details: [{ message: "snake case", category: "source-normalization" }],
    details: ["legacy detail"]
  });

  assert.deepEqual(result.conversionDetails, [
    { message: "snake case", category: "source-normalization" },
    { message: "legacy detail", category: "technical" }
  ]);
});

test("aggregate conversion details identify their output in multi-song conversions", () => {
  const details = aggregateConversionDetails([
    sanitizeConversionResult({
      outputPath: "D:\\FeedPaks\\First.feedpak",
      conversionDetails: [{ message: "bend normalized", category: "source-normalization" }]
    }),
    sanitizeConversionResult({
      outputPath: "D:\\FeedPaks\\Second.feedpak",
      conversionDetails: [{ message: "bend normalized", category: "source-normalization" }]
    })
  ], true);

  assert.deepEqual(details, [
    {
      message: "D:\\FeedPaks\\First.feedpak: bend normalized",
      category: "source-normalization"
    },
    {
      message: "D:\\FeedPaks\\Second.feedpak: bend normalized",
      category: "source-normalization"
    }
  ]);
});

test("structured technical details cannot leak back through legacy warning lines", () => {
  const result = sanitizeConversionResult({
    warnings: ["material warning"],
    conversionDetails: [
      { message: "bend normalized", category: "source-normalization" },
      { message: "high fret preserved", category: "chart-validation" }
    ]
  });

  assert.deepEqual(filterFallbackWarnings([
    "material warning",
    "bend normalized",
    "high fret preserved",
    "unstructured material warning"
  ], [result]), ["unstructured material warning"]);
});
