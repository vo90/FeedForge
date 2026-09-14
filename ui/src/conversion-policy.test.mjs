import assert from "node:assert/strict";
import test from "node:test";

import {
  SAFE_VALIDATION_POLICY,
  STRICT_VALIDATION_POLICY,
  chartWarningSummary,
  conversionOutputPaths,
  conversionOutcome,
  conversionResultStatus,
  normalizeOutputResults,
  outputLocationSummary,
  normalizeValidationPolicy
} from "./conversion-policy.mjs";

test("safe is the default validation policy and strict is opt-in", () => {
  assert.equal(normalizeValidationPolicy(undefined), SAFE_VALIDATION_POLICY);
  assert.equal(normalizeValidationPolicy("safe"), SAFE_VALIDATION_POLICY);
  assert.equal(normalizeValidationPolicy("STRICT"), STRICT_VALIDATION_POLICY);
  assert.equal(normalizeValidationPolicy("unexpected"), SAFE_VALIDATION_POLICY);
});

test("structured chart warning fields are normalized", () => {
  const result = conversionOutcome({
    warnings: ["Tone data was omitted."],
    convertedWithChartWarnings: true,
    chartWarningCount: 3,
    chartWarnings: [{ message: "Chart compatibility warning: fret 127 was preserved." }]
  });

  assert.equal(result.convertedWithChartWarnings, true);
  assert.equal(result.chartWarningCount, 3);
  assert.deepEqual(result.warnings, ["Tone data was omitted."]);
  assert.deepEqual(result.chartWarnings, ["Chart compatibility warning: fret 127 was preserved."]);
  assert.equal(chartWarningSummary({ converted_with_chart_warnings: true }), "Converted with 1 chart warning.");
});

test("ordinary warnings do not turn a conversion into a chart-warning outcome", () => {
  assert.deepEqual(conversionOutcome({ warnings: ["No tone definition was found."] }), {
    warnings: ["No tone definition was found."],
    chartWarnings: [],
    chartWarningCount: 0,
    convertedWithChartWarnings: false
  });
});

test("older explicitly labelled warning text is recognized defensively", () => {
  const result = conversionOutcome({
    warnings: ["Chart compatibility warning: unusual slide destination 28 was preserved."]
  });
  assert.equal(result.convertedWithChartWarnings, true);
  assert.equal(result.chartWarningCount, 1);
});

test("partial multi-song results keep sanitized per-output details", () => {
  const backendResult = {
    ok: false,
    partial: true,
    outputPaths: ["D:\\FeedPaks\\Artist A\\First.feedpak"],
    outputResults: [
      { outputPath: "D:\\FeedPaks\\Artist A\\First.feedpak", chartWarningCount: 2 },
      { output_path: "D:\\FeedPaks\\Artist B\\Second.feedpak", publishable: true },
      null,
      { chartWarningCount: 4 }
    ]
  };

  const outputResults = normalizeOutputResults(backendResult);
  const outputPaths = conversionOutputPaths(backendResult, outputResults);

  assert.deepEqual(outputResults, [
    { outputPath: "D:\\FeedPaks\\Artist A\\First.feedpak", chartWarningCount: 2 },
    {
      output_path: "D:\\FeedPaks\\Artist B\\Second.feedpak",
      outputPath: "D:\\FeedPaks\\Artist B\\Second.feedpak",
      publishable: true
    }
  ]);
  assert.deepEqual(outputPaths, [
    "D:\\FeedPaks\\Artist A\\First.feedpak",
    "D:\\FeedPaks\\Artist B\\Second.feedpak"
  ]);
  assert.equal(conversionResultStatus(backendResult, outputPaths), "partial");
  assert.equal(conversionResultStatus({ ok: true, partial: true }, []), "converted");
  assert.equal(conversionResultStatus({ ok: false, partial: true }, []), "failed");
});

test("output location summaries only name a folder when every output shares it", () => {
  assert.deepEqual(
    outputLocationSummary([
      "D:\\FeedPaks\\Artist A\\First.feedpak",
      "D:\\FeedPaks\\Artist A\\Second.feedpak"
    ]),
    {
      folders: ["D:\\FeedPaks\\Artist A"],
      folderCount: 1,
      suffix: " in D:\\FeedPaks\\Artist A"
    }
  );
  assert.deepEqual(
    outputLocationSummary([
      "D:\\FeedPaks\\Artist A\\First.feedpak",
      "D:\\FeedPaks\\Artist B\\Second.feedpak"
    ]),
    {
      folders: ["D:\\FeedPaks\\Artist A", "D:\\FeedPaks\\Artist B"],
      folderCount: 2,
      suffix: " across 2 folders"
    }
  );
});
