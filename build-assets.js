const fs = require('fs');
const path = require('path');

const assets = {
  'alpinejs/dist/cdn.min.js': 'alpine.min.js',
  'chart.js/dist/chart.umd.js': 'chart.umd.js',
  'chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js': 'chartjs-adapter-date-fns.bundle.min.js',
  'socket.io-client/dist/socket.io.min.js': 'socket.io.min.js'
};

const distDir = path.join(__dirname, 'app', 'static', 'dist');

if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

for (const [src, dest] of Object.entries(assets)) {
  const srcPath = path.join(__dirname, 'node_modules', src);
  const destPath = path.join(distDir, dest);
  fs.copyFileSync(srcPath, destPath);
  console.log(`Copied ${src} to ${destPath}`);
}
