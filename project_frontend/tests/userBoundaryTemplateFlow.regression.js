const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const sourcePath = path.resolve(__dirname, "../src/app/page.tsx");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled =
  ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: sourcePath,
  }).outputText +
  `
module.exports.__flowTest = {
  dataUrlToFile,
  buildTemplateCanvasImages,
  templateFieldsToWorkspaceRois,
  parseHtmlTableStructured,
};
`;

const detectionResponse = {
  queryId: "query-1",
  engine: "layout-signature",
  version: "phase-test",
  threshold: 0.75,
  matched: true,
  bestCandidate: {
    templateId: "template-1",
    templateName: "Invoice",
    score: 0.91,
    finalScore: 0.93,
    finalPassed: true,
    templateRois: [
      {
        fieldId: "field-1",
        fieldName: "total",
        displayLabel: "Total",
        pageNumber: 1,
        dataType: "text",
        extractionMethod: "paddle_thai_ocr",
        roi: { x_ratio: 0.1, y_ratio: 0.2, width_ratio: 0.3, height_ratio: 0.1 },
      },
    ],
    projectedFields: [
      {
        fieldId: "field-1",
        fieldName: "total",
        displayLabel: "Total",
        pageNumber: 1,
        projectedRoi: { page_number: 1, x_ratio: 0.1, y_ratio: 0.2, width_ratio: 0.3, height_ratio: 0.1 },
      },
    ],
    extractionTest: {
      templateId: "template-1",
      status: "passed",
      passed: true,
      testedCount: 1,
      passedCount: 1,
      failedCount: 0,
      fields: [{ fieldId: "field-1", fieldName: "total", passed: true }],
    },
    alignedImagePreviewUrl: "data:image/png;base64,YWxpZ25lZA==",
  },
  candidates: [
    {
      templateId: "template-1",
      templateName: "Invoice",
      score: 0.91,
      templateRois: [],
      projectedFields: [],
    },
  ],
  pages: [
    {
      pageIndex: 1,
      matched: true,
      bestCandidate: {
        templateId: "template-1",
        templateName: "Invoice",
        score: 0.91,
        alignedImagePreviewUrl: "data:image/png;base64,YWxpZ25lZA==",
        templateRois: [],
        projectedFields: [],
      },
      candidates: [
        {
          templateId: "template-1",
          templateName: "Invoice",
          score: 0.91,
          alignedImagePreviewUrl: "data:image/png;base64,YWxpZ25lZA==",
          templateRois: [],
          projectedFields: [],
        },
      ],
    },
  ],
};

const templateBundle = {
  template: { id: "template-1", name: "Invoice" },
  pages: [{ id: "page-1", pageNumber: 1 }],
  fields: [
    {
      id: "field-1",
      pageNumber: 1,
      fieldName: "total",
      displayLabel: "Total",
      defaultSelected: true,
      useForVerification: false,
      dataType: "text",
      extractionMethod: "paddle_thai_ocr",
      sortOrder: 1,
      roi: { pageNumber: 1, xRatio: 0.1, yRatio: 0.2, widthRatio: 0.3, heightRatio: 0.1 },
    },
    {
      id: "field-2",
      pageNumber: 1,
      fieldName: "items",
      displayLabel: "Items",
      defaultSelected: true,
      useForVerification: false,
      dataType: "table",
      extractionMethod: "table_recognition_v2",
      sortOrder: 2,
      roi: { pageNumber: 1, xRatio: 0.2, yRatio: 0.4, widthRatio: 0.5, heightRatio: 0.2 },
    },
  ],
};

class TestImage {
  constructor() {
    this.complete = false;
    this.naturalWidth = 1000;
    this.naturalHeight = 1400;
  }

  set src(value) {
    this._src = value;
    this.complete = true;
    queueMicrotask(() => this.onload && this.onload());
  }

  get src() {
    return this._src;
  }
}

global.Image = TestImage;
global.DOMParser = class DOMParser {
  parseFromString(html) {
    const rowMatches = [...String(html).matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)];
    const rows = rowMatches.map((rowMatch) => {
      const cellMatches = [...rowMatch[1].matchAll(/<(td|th)([^>]*)>([\s\S]*?)<\/\1>/gi)];
      return {
        querySelectorAll(selector) {
          if (selector !== "th,td") return [];
          return cellMatches.map((cellMatch) => ({
            textContent: cellMatch[3].replace(/<[^>]+>/g, ""),
            getAttribute(name) {
              const attrMatch = cellMatch[2].match(new RegExp(`${name}=['"]?([^'">\\s]+)`, "i"));
              return attrMatch ? attrMatch[1] : null;
            },
          }));
        },
      };
    });
    return {
      querySelectorAll(selector) {
        if (selector === "tr") return rows;
        if (selector === "thead tr") return [];
        return [];
      },
    };
  }
};
global.fetch = async (url) => {
  if (String(url).startsWith("data:")) {
    return {
      ok: true,
      blob: async () => new Blob(["image"], { type: "image/jpeg" }),
    };
  }
  throw new Error(`Unexpected fetch in regression test: ${url}`);
};

const testModule = new Module(sourcePath, module);
testModule.filename = sourcePath;
testModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
testModule.require = (request) => {
  if (request === "react") return { useEffect() {}, useMemo: (factory) => factory(), useState: (value) => [value, () => {}] };
  if (request === "next/dynamic") return () => function DynamicComponent() {};
  if (request === "next/navigation") return { useRouter: () => ({ replace() {} }) };
  if (request === "lucide-react") return { LogOut: function LogOut() {} };
  if (request.includes("../user/components/")) return function MockComponent() {};
  if (request === "../shared/ui") return { InlineState: function InlineState() {} };
  if (request === "../auth/AuthGate") return function AuthGate(props) { return props.children; };
  if (request === "../auth/session") return { clearAuthSession() {}, readAuthSession: () => ({ role: "user", email: "user@ocr.com" }) };
  if (request === "../types/ocr") return {};
  if (request === "../admin/adminApi") {
    return {
      ADMIN_API_BASE_URL: "http://localhost:8000",
      detectTemplateDev: async () => detectionResponse,
      fetchTemplateBundle: async () => templateBundle,
    };
  }
  return require(request);
};
testModule._compile(compiled, sourcePath);

(async () => {
  const { dataUrlToFile, buildTemplateCanvasImages, templateFieldsToWorkspaceRois, parseHtmlTableStructured } = testModule.exports.__flowTest;
  const sourceImages = ["data:image/jpeg;base64,c291cmNl"];

  const file = await dataUrlToFile(sourceImages[0], "confirmed-document.jpg");
  assert.equal(file.name, "confirmed-document.jpg");

  const templateCanvasImages = await buildTemplateCanvasImages(sourceImages, detectionResponse, "template-1");
  const workspaceRois = await templateFieldsToWorkspaceRois(templateBundle.fields, templateCanvasImages, detectionResponse, "template-1");
  const matchedTemplate = {
    id: templateBundle.template.id,
    name: templateBundle.template.name,
    confidence: detectionResponse.bestCandidate.finalScore ?? detectionResponse.bestCandidate.score ?? null,
    decisionReason: detectionResponse.bestCandidate.decisionReason ?? null,
    alignmentStatus: detectionResponse.bestCandidate.alignmentStatus ?? null,
  };

  assert.equal(templateCanvasImages.length, 1);
  assert.equal(templateCanvasImages[0], detectionResponse.bestCandidate.alignedImagePreviewUrl);
  assert.equal(workspaceRois.length, 2);
  assert.equal(workspaceRois[0].fieldName, "Total");
  assert.equal(workspaceRois[1].type, "table");
  assert.deepEqual(matchedTemplate, {
    id: "template-1",
    name: "Invoice",
    confidence: 0.93,
    decisionReason: null,
    alignmentStatus: null,
  });

  const structuredTable = parseHtmlTableStructured(
    "<table><tr><th rowspan='2'>A</th><th colspan='2'>B</th></tr><tr><td>C</td><td>D</td></tr></table>"
  );
  assert.equal(structuredTable.cells.find((cell) => cell.row === 0 && cell.col === 0).rowSpan, 2);
  assert.equal(structuredTable.cells.find((cell) => cell.row === 0 && cell.col === 1).colSpan, 2);
  assert.equal(structuredTable.cells.find((cell) => cell.row === 1 && cell.col === 0).hidden, true);
  assert.deepEqual(structuredTable.rows, [
    ["A", "B", ""],
    ["", "C", "D"],
  ]);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
