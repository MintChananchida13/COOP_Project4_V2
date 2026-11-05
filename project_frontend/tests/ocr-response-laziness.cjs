const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Execute the actual expressions from the page, without loading React/browser state.
const source = fs.readFileSync(path.join(__dirname, '../src/app/page.tsx'), 'utf8');
function expression(start, end, parameters, result) {
  const offset = source.indexOf(start);
  assert.ok(offset >= 0);
  const stop = source.indexOf(end, offset);
  assert.ok(stop > offset);
  const javascript = ts.transpileModule(source.slice(offset, stop), {
    compilerOptions: {target: ts.ScriptTarget.ES2022},
  }).outputText;
  return new Function(...parameters, javascript + '\nreturn ' + result + ';');
}
const selectStructured = expression('const responseStructured =', 'const responseRowsFromCells =',
  ['resItem', 'parseHtmlTableStructured'], 'responseStructured');
const selectRows = expression('const rawTableRows =', 'const finalTableStructured =',
  ['resItem', 'responseRowsFromCells', 'responseStructured', 'parseHtmlTableRows'], 'rawTableRows');
const selectText = expression('const extractedText = String(resItem.text', 'const responseRoiId =',
  ['resItem', 'rawTableRows', 'tableRowsToMarkdown'], 'extractedText');

test('structured data wins without parsing HTML; both API spellings are supported', () => {
  const structured = {cells: [{text: 'unchanged'}]};
  const fail = () => { throw new Error('unnecessary HTML parsing'); };
  assert.equal(selectStructured({table_structured: structured, table_html: '<table>'}, fail), structured);
  assert.equal(selectStructured({tableStructured: structured, table_html: '<table>'}, fail), structured);
});

test('HTML fallback is parsed exactly once when structured data is absent', () => {
  let calls = 0;
  const value = {rows: [['same']]};
  assert.equal(selectStructured({table_html: '<table>'}, html => {
    calls++;
    assert.equal(html, '<table>');
    return value;
  }), value);
  assert.equal(calls, 1);
});

test('rows from cells avoid converting unused table_rows and parsing HTML', () => {
  const rows = [['unchanged']];
  const response = {get table_rows() {throw new Error('unnecessary conversion');}};
  assert.equal(selectRows(response, rows, {}, () => {throw new Error('HTML');}), rows);
});

test('table_rows and HTML fallbacks keep their original priority and values', () => {
  assert.deepEqual(selectRows({table_rows: [[1, null]]}, null, undefined, () => {throw new Error('HTML');}), [['1', '']]);
  const htmlRows = [['HTML fallback']];
  assert.equal(selectRows({}, null, undefined, () => htmlRows), htmlRows);
});

test('existing OCR text avoids Markdown generation', () => {
  assert.equal(selectText({text: 'original OCR text'}, [['data']], () => {throw new Error('unnecessary Markdown');}), 'original OCR text');
});

test('Markdown and empty-text fallbacks are preserved', () => {
  let calls = 0;
  assert.equal(selectText({text: ''}, [['data']], rows => {calls++; return '| data |';}), '| data |');
  assert.equal(calls, 1);
  assert.equal(selectText({}, [], () => {throw new Error('empty');}), '');
});
