import { spawn } from 'node:child_process';
const processes = [
  spawn(process.execPath, ['--env-file-if-exists=.env', 'server/index.mjs'], { stdio: 'inherit' }),
  spawn(process.execPath, ['--env-file-if-exists=.env', 'server/worker.mjs'], { stdio: 'inherit' }),
  spawn(process.execPath, ['node_modules/vite/bin/vite.js'], { stdio: 'inherit' }),
];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of processes) child.kill();
  process.exit(code);
}
for (const child of processes) child.on('exit', (code) => stop(code || 0));
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
