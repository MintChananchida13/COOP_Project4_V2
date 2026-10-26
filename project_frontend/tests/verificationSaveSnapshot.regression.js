const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadFunctions(relativeFile, names, context) {
  const filename = path.resolve(__dirname, relativeFile);
  const source = fs.readFileSync(filename, "utf8");
  const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const declarations = new Map();
  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && names.includes(node.name.text)) {
      declarations.set(node.name.text, `const ${node.getText(tree)};`);
    }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  names.forEach((name) => assert.ok(declarations.has(name), `Missing ${name}`));
  const compiled = ts.transpileModule(names.map((name) => declarations.get(name)).join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  return new Function(...Object.keys(context), `${compiled}\nreturn {${names.join(",")}};`)(...Object.values(context));
}

const field = (patch = {}) => ({
  id: "field-1", pageNumber: 1, fieldName: "Anchor", dataType: "text",
  extractionMethod: "paddle_thai_ocr", useForVerification: true, expectedText: "draft",
  roi: { xRatio: 0.1, yRatio: 0.2, widthRatio: 0.3, heightRatio: 0.1 }, ...patch,
});

function editorContext(initialFields) {
  const queuedFields = [];
  return {
    queuedFields,
    pendingDetectionMode: null,
    pendingMainPageNumber: null,
    setSelectedTemplate() {}, setSelectedTemplatePages() {}, setSelectedIgnoreRegions() {},
    setSelectedTemplateFields(value) { queuedFields.push(value); },
    selectedTemplateFieldsRef: { current: initialFields },
    pendingDeletedFieldIdsRef: { current: new Set() },
    dirtyFieldPatchesRef: { current: new Map() },
    pendingLocalFieldPatchesRef: { current: new Map() },
    cancelledLocalFieldIdsRef: { current: new Set() },
    canPersistToBackend: true, templateId: "template-1", setLocalOnly() {}, setSaved() {},
  };
}

test("verification starts with the saved snapshot even before React commits the new state", async () => {
  const original = field();
  const saved = field({ expectedText: "normalized saved value", roi: { ...original.roi, xRatio: 0.100001 } });
  const context = editorContext([original]);
  context.dirtyFieldPatchesRef.current.set(original.id, { expectedText: "edited" });
  context.updateTemplateFieldApi = async () => ({ template: {}, pages: [], fields: [saved], ignoreRegions: [] });
  const functions = loadFunctions("../src/admin/AdminTemplateEditPage.tsx", ["applyBundle", "flushFieldDrafts"], context);
  const returned = await functions.flushFieldDrafts();
  let rendered = [original];
  for (const queued of context.queuedFields) {
    rendered = typeof queued === "function" ? queued(rendered) : queued;
  }
  assert.deepEqual(returned, rendered);
  assert.equal(returned[0].expectedText, saved.expectedText);
  assert.strictEqual(returned, context.selectedTemplateFieldsRef.current);
});

test("bundle refresh preserves pending local fields and dirty edits while excluding deleted fields", () => {
  const local = field({ id: "local_field_2" });
  const context = editorContext([field(), local]);
  context.pendingLocalFieldPatchesRef.current.set(local.id, local);
  context.dirtyFieldPatchesRef.current.set("field-1", { expectedText: "pending edit" });
  context.pendingDeletedFieldIdsRef.current.add("deleted");
  const { applyBundle } = loadFunctions("../src/admin/AdminTemplateEditPage.tsx", ["applyBundle"], context);
  const result = applyBundle({ template: {}, pages: [], fields: [field(), field({ id: "deleted" })], ignoreRegions: [] });
  assert.deepEqual(result.map((item) => item.id), ["field-1", "local_field_2"]);
  assert.equal(result[0].expectedText, "pending edit");
  assert.strictEqual(context.selectedTemplateFieldsRef.current, result);
});

test("a real verification edit during inference still invalidates the result", async () => {
  let complete;
  const response = new Promise((resolve) => { complete = resolve; });
  const errors = [];
  const fields = [field()];
  const signatureRef = { current: "initial" };
  const context = {
    fields, verificationAnchorsReady: true, verificationBlockedMessage: "",
    extractionTestSignature: "", verificationTestSignature: "initial",
    extractionTestSignatureRef: { current: "" }, verificationTestSignatureRef: signatureRef,
    isAnchor: (item) => item.useForVerification,
    onBeforeRunTest: async () => fields,
    testTemplateVerificationAnchors: async () => response,
    testTemplateExtractionFields() {}, templateId: "template-1", pages: [],
    setTestAction() {}, setTestStatus() {}, setTestResult() {}, setTestResultKind() {},
    setTestError(message) { errors.push(message); },
  };
  const { runStepTest } = loadFunctions("../src/admin/workspace/WorkspaceTemplateEditorV2.tsx", ["verificationAnchorTestSignature", "runStepTest"], context);
  const running = runStepTest("verification");
  await Promise.resolve();
  signatureRef.current = "user changed the ROI";
  complete({ passedCount: 1, testedCount: 1 });
  await running;
  assert.ok(errors.some((message) => message.includes("Verification ROI")));
});
