import { spawn } from 'node:child_process';
const processes = ['server/index.mjs', 'server/worker.mjs'].map((file) =>
  spawn(process.execPath, ['--env-file-if-exists=.env', file], { stdio: 'inherit' }),
);
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
