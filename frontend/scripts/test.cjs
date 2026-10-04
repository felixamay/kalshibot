// Use the existing TypeScript compiler to run tests with Node's test API.
const fs = require('node:fs');
const ts = require('typescript');
const Module = require('node:module');
const path = require('node:path');
for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (module, filename) => {
    const result = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true }
    });
    module._compile(result.outputText, filename);
  };
}
const resolve = Module._resolveFilename;
Module._resolveFilename = function (name, ...args) {
  return resolve.call(this, name.startsWith('@/') ? path.join(__dirname, '../src', name.slice(2)) : name, ...args);
};
for (const dir of ['lib', 'components']) {
  const root = path.join(__dirname, '../src', dir);
  for (const file of fs.readdirSync(root).filter(f => /\.test\.tsx?$/.test(f))) require(path.join(root, file));
}
