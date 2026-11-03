import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

const source = fs.readFileSync(new URL('../src/shared/workspace/WorkspaceCustomEditor.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('WorkspaceCustomEditor.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const editor = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'WorkspaceCustomEditor');
// Execute the actual selection-reset hooks without requiring a browser or the rest of the editor.
const hooks = editor.body.statements.filter(node => {
  if (ts.isVariableStatement(node)) return node.declarationList.declarations.some(item => item.name.getText(ast) === 'selectionSetterRef');
  return ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
    && node.expression.expression.getText(ast) === 'useEffect' && node.getText(ast).includes('selectionSetterRef.current');
});
assert.equal(hooks.length, 3);
const compiled = ts.transpileModule(`function render(setSelectedId, currentIndex) { ${hooks.map(node => node.getText(ast)).join('\n')} }`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020 },
}).outputText;

test('admin callback changes keep selection and active gestures; page changes reset both', () => {
  const slots = [];
  let cursor = 0;
  let pending = [];
  let cancelled = 0;
  let selected = 42;
  let resetCalls = 0;
  const useRef = value => {
    const index = cursor++;
    return slots[index] ??= { current: value };
  };
  const useEffect = (effect, dependencies) => {
    const index = cursor++;
    const previous = slots[index];
    if (previous && dependencies.every((value, i) => Object.is(value, previous.dependencies[i]))) return;
    pending.push(() => {
      previous?.cleanup?.();
      slots[index] = { dependencies, cleanup: effect() };
    });
  };
  const render = new Function('useRef', 'useEffect', 'cancelSelectionGesture', `${compiled}; return render;`)(
    useRef, useEffect, () => cancelled++);
  const rerender = page => {
    cursor = 0;
    pending = [];
    // Match Admin's fresh callback identity on every render.
    render(value => { selected = value; resetCalls++; }, page);
    pending.forEach(effect => effect());
  };
  rerender(0);
  assert.equal(selected, null);
  selected = 42;
  rerender(0);
  rerender(0);
  assert.equal(selected, 42);
  assert.equal(resetCalls, 1);
  assert.equal(cancelled, 0);
  rerender(1);
  assert.equal(selected, null);
  assert.equal(resetCalls, 2);
  assert.equal(cancelled, 1);
});
