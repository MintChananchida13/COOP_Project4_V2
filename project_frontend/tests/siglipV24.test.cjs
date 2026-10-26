const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const trace = {
  scoring_version: 'siglip-single-prompt-v2.4', raw_top1_category: 'thai_garuda', raw_top1_logit: 8.1,
  raw_top2_category: 'emblem', raw_top2_logit: 6.5818, positive_top1_logit: 8.1,
  negative_top1_category: 'printed_text', negative_top1_logit: -10,
  positive_negative_margin: 18.1, positive_top1_top2_margin: 1.5182,
  positive_relative_score: 0.8203, positive_relative_margin: 0.6405,
  final_predicted_category: 'thai_garuda', decision_rule: 'rank1_negative_competition_then_optional_relative_gates',
  decision_reason: 'positive_rank1_accepted', negative_gate_passed: true,
  confidence_gate_passed: 'not_applied', margin_gate_passed: 'not_applied',
  decision_config: { confidence_min_relative_score: null, margin_min_relative_score: null },
};

test('frontend maps final decision without selecting prediction from UI percentages', () => {
  const filename = path.resolve(__dirname, '../src/admin/adminApi.ts');
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true);
  const mapper = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'mapTemplateStepTestItem');
  const code = ts.transpileModule(`${mapper.getText(source)}\nglobalThis.map = mapTemplateStepTestItem;`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({});
  vm.runInContext(code, context);
  const rejected = { ...trace, final_predicted_category: 'other', decision_reason: 'negative_category_won' };
  const item = context.map({ passed: false, predicted_image_category: 'other', siglip_decision: rejected,
    siglip_target_rank: 1, siglip_ui_percentages: [{ image_category: 'thai_garuda', percentage: 82.0 }] });
  assert.equal(item.predictedImageCategory, 'other');
  assert.equal(item.siglipDecision.final_predicted_category, 'other');
  assert.equal(item.siglipDecision.decision_reason, 'negative_category_won');
  assert.equal(item.siglipTargetRank, 1);
  assert.equal(context.map({}).siglipDecision, null);
});

test('server-rendered diagnostic UI separates ranking, prediction, reason, gates and verification', () => {
  const filename = path.resolve(__dirname, '../src/shared/SiglipDecisionDetails.tsx');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require });
  const html = renderToStaticMarkup(React.createElement(exports.default, { decision: trace, verificationPassed: true }));
  for (const label of ['Raw Positive Ranking', 'Final Prediction', 'Decision Reason', 'Verification Result',
    'not_applied', 'not probability', 'raw logits', 'siglip-single-prompt-v2.4', 'disabled']) {
    assert.ok(html.includes(label), label);
  }
  assert.equal(renderToStaticMarkup(React.createElement(exports.default, { verificationPassed: false })), '');
  const parent = fs.readFileSync(path.resolve(__dirname, '../src/admin/workspace/WorkspaceTemplateEditorV2.tsx'), 'utf8');
  assert.ok(parent.includes('<SiglipDecisionDetails decision={item.siglipDecision}'));
});
