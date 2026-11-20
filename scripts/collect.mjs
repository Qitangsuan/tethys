#!/usr/bin/env node
// TETHYS v2 采集器：~/.dsh/sessions 多帧 zstd → data/raw/*.jsonl
// 契约（v0.2 §2）：按 zstd magic 切帧逐帧解压；输出 raw jsonl 供判定器消费。
import { readFileSync, writeFileSync, existsSync, statSync, mkdirSync, readdirSync } from "node:fs";
import { zstdDecompressSync } from "node:zlib";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const FORCE = process.argv.includes("--force");

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
const DSH_HOME = join(homedir(), ".dsh");
const SESSIONS = join(DSH_HOME, "sessions");
const RAW_DIR = join(ROOT, "data", "raw");
mkdirSync(RAW_DIR, { recursive: true });

/** 递归找所有会话文件 */
function findSessionFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...findSessionFiles(p));
    else if (entry.name.endsWith(".jsonl.zstd")) out.push(p);
  }
  return out;
}

/** 按 magic 切帧并逐帧解压 */
function decompressFrames(buf) {
  const pos = [];
  let i = 0;
  while ((i = buf.indexOf(MAGIC, i)) !== -1) {
    pos.push(i);
    i += 4;
  }
  const parts = [];
  let ok = 0;
  let fail = 0;
  for (let k = 0; k < pos.length; k++) {
    const start = pos[k];
    const end = k + 1 < pos.length ? pos[k + 1] : buf.length;
    try {
      parts.push(zstdDecompressSync(buf.subarray(start, end)));
      ok++;
    } catch {
      fail++;
    }
  }
  return { data: Buffer.concat(parts), frames: pos.length, ok, fail };
}

/** 从事件流提取会话元信息 */
function extractMeta(lines) {
  const meta = { type: null, sessionId: null, createdAt: null, cwd: null, turns: 0, steps: 0, tools: 0 };
  for (const line of lines) {
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    if (o.type === "session" && !meta.sessionId) {
      meta.sessionId = o.id ?? null;
      meta.createdAt = o.createdAt ?? null;
      meta.cwd = o.cwd ?? null;
    } else if (o.type === "turn/end") meta.turns++;
    else if (o.type === "step/end") meta.steps++;
    else if (o.type === "tool/call") meta.tools++;
  }
  return meta;
}

const files = findSessionFiles(SESSIONS);
const index = [];
let collected = 0;
let skipped = 0;
let totalBytes = 0;
let totalFrames = 0;
let badFrames = 0;

for (const src of files) {
  // 会话目录形如 session-<uuid>/session.v4.jsonl.zstd
  const sessionDir = basename(dirname(src));
  const dest = join(RAW_DIR, `${sessionDir}.jsonl`);
  const st = statSync(src);

  if (!FORCE && existsSync(dest) && statSync(dest).mtimeMs >= st.mtimeMs) {
    skipped++;
    const lines = readFileSync(dest, "utf8").split("\n").filter(Boolean);
    const meta = extractMeta(lines);
    index.push({ file: basename(dest), src, ...meta, bytes: st.size, lines: lines.length, source: "cache" });
    continue;
  }

  const buf = readFileSync(src);
  const { data, frames, fail } = decompressFrames(buf);
  totalFrames += frames;
  badFrames += fail;
  const text = data.toString("utf8");
  writeFileSync(dest, text, "utf8");
  const lines = text.split("\n").filter(Boolean);
  const meta = extractMeta(lines);
  totalBytes += data.length;
  collected++;
  index.push({ file: basename(dest), src, ...meta, bytes: data.length, frames, fail, lines: lines.length, source: "fresh" });
}

writeFileSync(
  join(RAW_DIR, "index.json"),
  JSON.stringify({ generatedAt: Date.now(), sessions: index }, null, 2),
  "utf8",
);
console.log(
  `[collect] 会话 ${files.length} 个 → 新解压 ${collected}，复用 ${skipped}；解压字节 ${(totalBytes / 1048576).toFixed(1)} MB，帧 ${totalFrames}，坏帧 ${badFrames}`,
);
console.log(`[collect] 索引写入 data/raw/index.json`);
