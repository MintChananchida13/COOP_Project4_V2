const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const ts = require("typescript");

const sourcePath = path.resolve(__dirname, "../src/admin/adminApi.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;

const testModule = new Module(sourcePath, module);
testModule.filename = sourcePath;
testModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
testModule.require = (request) => {
  if (request === "../types/ocr") return {};
  return require(request);
};
testModule._compile(compiled, sourcePath);

const { detectTemplateDev } = testModule.exports;

global.fetch = async () => ({
  ok: true,
  json: async () => ({
    data: {
      query_id: "query-1",
      engine: "layout-signature",
      version: "phase-test",
      threshold: 0.75,
      matched: true,
      best_candidate: {
        template_id: "template-1",
        template_name: "Invoice",
        score: 0.91,
        final_score: 0.93,
        final_passed: true,
        template_rois: [
          {
            field_id: "field-1",
            field_name: "total",
            display_label: "Total",
            page_number: 1,
            data_type: "text",
            extraction_method: "paddle_thai_ocr",
            roi: { x_ratio: 0.1, y_ratio: 0.2, width_ratio: 0.3, height_ratio: 0.1 },
          },
        ],
        projected_fields: [
          {
            field_id: "field-1",
            field_name: "total",
            display_label: "Total",
            page_number: 1,
            projected_roi: { page_number: 1, x_ratio: 0.1, y_ratio: 0.2, width_ratio: 0.3, height_ratio: 0.1 },
          },
        ],
        extraction_test: {
          template_id: "template-1",
          status: "passed",
          passed: true,
          score: 1,
          tested_count: 1,
          passed_count: 1,
          failed_count: 0,
          fields: [
            {
              field_id: "field-1",
              field_name: "total",
              display_label: "Total",
              page_number: 1,
              data_type: "text",
              extraction_method: "paddle_thai_ocr",
              ocr_text: "100.00",
              confidence: 0.99,
              passed: true,
              status: "passed",
            },
          ],
        },
      },
      candidates: [
        {
          template_id: "template-1",
          template_name: "Invoice",
          score: 0.91,
          final_score: 0.93,
          template_rois: [],
          projected_fields: [],
        },
      ],
      pages: [
        {
          page_index: 0,
          matched: true,
          best_candidate: {
            template_id: "template-1",
            template_name: "Invoice",
            score: 0.91,
            template_rois: [],
            projected_fields: [],
          },
          candidates: [
            {
              template_id: "template-1",
              template_name: "Invoice",
              score: 0.91,
              template_rois: [],
              projected_fields: [],
            },
          ],
        },
      ],
    },
  }),
});

(async () => {
  const file = new File(["demo"], "demo.png", { type: "image/png" });
  const result = await detectTemplateDev(file);

  assert.equal(result.matched, true);
  assert.equal(result.bestCandidate.templateId, "template-1");
  assert.equal(result.bestCandidate.templateRois.length, 1);
  assert.equal(result.bestCandidate.projectedFields.length, 1);
  assert.equal(result.bestCandidate.extractionTest.testedCount, 1);
  assert.equal(result.candidates.length, 1);
  assert.equal(result.pages.length, 1);
  assert.equal(result.pages[0].bestCandidate.templateId, "template-1");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
