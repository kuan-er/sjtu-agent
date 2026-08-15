import { build } from 'esbuild';
import { copyFileSync, cpSync, readFileSync, writeFileSync } from 'node:fs';

await build({
  entryPoints: ['webui/src/main.jsx'],
  bundle: true,
  minify: true,
  format: 'iife',
  jsx: 'automatic',
  outfile: 'sjtu_agent/web/static/app.js',
  define: {
    'process.env.NODE_ENV': '"production"',
  },
  legalComments: 'none',
});

const katexCss = readFileSync('node_modules/katex/dist/katex.min.css', 'utf8');
const hljsCss = readFileSync('node_modules/highlight.js/styles/github-dark.min.css', 'utf8');
const appCss = readFileSync('webui/src/style.css', 'utf8');
writeFileSync('sjtu_agent/web/static/app.css', katexCss + '\n' + hljsCss + '\n' + appCss);

cpSync('node_modules/katex/dist/fonts', 'sjtu_agent/web/static/fonts', { recursive: true });
copyFileSync('webui/index.html', 'sjtu_agent/web/static/index.html');
console.log('webui build complete -> sjtu_agent/web/static/');
