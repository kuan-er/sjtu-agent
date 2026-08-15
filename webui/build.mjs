import { build } from 'esbuild';
import { copyFileSync } from 'node:fs';

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

copyFileSync('webui/src/style.css', 'sjtu_agent/web/static/app.css');
copyFileSync('webui/index.html', 'sjtu_agent/web/static/index.html');
console.log('webui build complete -> sjtu_agent/web/static/');
