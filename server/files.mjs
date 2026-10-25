import { mkdirSync, lstatSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { root } from './db.mjs';
export const workspace = path.join(root, 'workspace');
mkdirSync(workspace, { recursive: true });
const extensions = new Set(['.txt', '.md', '.csv', '.json']);
export function filePath(name) {
  if (
    typeof name !== 'string' ||
    name.length > 120 ||
    !/^[\p{L}\p{N} _().-]+$/u.test(name) ||
    name.startsWith('.') ||
    /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(name) ||
    !extensions.has(path.extname(name).toLowerCase())
  )
    throw new Error('文件名不合法，仅支持工作目录中的 txt/md/csv/json 文件。');
  const target = path.join(workspace, name);
  try {
    if (lstatSync(target).isSymbolicLink() || !lstatSync(target).isFile())
      throw new Error('不允许链接或目录。');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return target;
}
export function listFiles() {
  return readdirSync(workspace).filter((name) => {
    try {
      return lstatSync(filePath(name)).isFile();
    } catch {
      return false;
    }
  });
}
export function readFile(name) {
  const file = filePath(name);
  if (lstatSync(file).size > 200_000) throw new Error('文件超过 200 KB 限制。');
  return readFileSync(file, 'utf8');
}
export function writeFile(name, content) {
  if (typeof content !== 'string' || Buffer.byteLength(content) > 200_000)
    throw new Error('内容超过 200 KB 限制。');
  writeFileSync(filePath(name), content, { encoding: 'utf8', flag: 'wx' });
  return { name, url: `/api/files/${encodeURIComponent(name)}` };
}
