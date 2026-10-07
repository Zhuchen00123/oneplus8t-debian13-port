// Bounded, offline CPU smoke test. Run each modality in a fresh process.
import fs from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { AutoConfig, AutoModel, AutoTokenizer, AutoProcessor, RawImage, env } from '@huggingface/transformers';

const mode = process.argv[2] ?? 'text';
if (!['text', 'vision'].includes(mode)) throw new Error('Expected text or vision');
const root = process.cwd();
const modelPath = path.join(root, 'models');
const threads = Number(process.env.EG_THREADS ?? 1);
const pauseMs = Number(process.env.EG_PAUSE_MS ?? 3000);
const shortProfile = process.env.EG_PROFILE === 'short';
const pause = () => new Promise(resolve => setTimeout(resolve, pauseMs));
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.useFSCache = false;
const report = {
  mode, started_at: new Date().toISOString(), runtime: env.version, node: process.version,
  architecture: process.arch, backend: 'CPU', dtype: 'q8', intra_op_threads: threads,
  inter_op_threads: 1, pause_ms: pauseMs, cpu_affinity: fs.readFileSync('/proc/self/status', 'utf8').match(/^Cpus_allowed_list:\s*(.*)$/m)?.[1],
  profile: shortProfile ? 'short' : 'standard', samples: [], snapshots: [], checks: {},
};
fs.mkdirSync(path.join(root, 'results'), { recursive: true });
function checkpoint() {
  fs.writeFileSync(path.join(root, 'results', `${mode}.json`), JSON.stringify(report, null, 2));
}
function snapshot(label) {
  const temperatures = [];
  for (const name of fs.readdirSync('/sys/class/thermal').filter(x => x.startsWith('thermal_zone'))) {
    try {
      const dir = `/sys/class/thermal/${name}`;
      temperatures.push({ type: fs.readFileSync(`${dir}/type`, 'utf8').trim(),
        celsius: Number(fs.readFileSync(`${dir}/temp`, 'utf8')) / 1000 });
    } catch {}
  }
  let battery = null;
  try { battery = Number(fs.readFileSync('/sys/class/power_supply/bq27541-0/temp', 'utf8')) / 10; } catch {}
  const snap = { label, at: new Date().toISOString(), memory: process.memoryUsage(),
    max_rss_kib: process.resourceUsage().maxRSS, battery_celsius: battery, temperatures };
  report.snapshots.push(snap);
  checkpoint();
  console.log(JSON.stringify({ stage: label, rss_mib: +(snap.memory.rss / 1048576).toFixed(1), battery_celsius: battery }));
  if (label !== 'disposed' && (battery >= 41 || temperatures.some(t => t.celsius >= 65 && t.celsius < 200))) {
    throw new Error('Bounded trial temperature stop threshold reached');
  }
}
function validate(tensor) {
  if (!tensor || tensor.dims.at(-1) !== 768) throw new Error('Expected 768-dimensional embeddings');
  const rows = tensor.tolist();
  for (const row of rows) {
    if (!row.every(Number.isFinite)) throw new Error('Nonfinite embedding');
    const norm = Math.hypot(...row);
    if (Math.abs(norm - 1) > .01) throw new Error(`Embedding norm ${norm}`);
  }
  return rows;
}
const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0);
let model;
try {
  snapshot('before_load');
  const config = await AutoConfig.from_pretrained(modelPath, { local_files_only: true });
  config.audio_config = null;
  if (mode === 'text') config.vision_config = null;
  const start = performance.now();
  model = await AutoModel.from_pretrained(modelPath, {
    config, local_files_only: true, device: 'cpu', dtype: 'q8',
    session_options: { intraOpNumThreads: threads, interOpNumThreads: 1, executionMode: 'sequential' },
  });
  report.load_ms = performance.now() - start;
  snapshot('loaded');
  if (mode === 'text') {
    const tokenizer = await AutoTokenizer.from_pretrained(modelPath, { local_files_only: true });
    async function encode(texts, label, options = {}) {
      await pause();
      snapshot(`before_${label}`);
      const begin = performance.now();
      const inputs = await tokenizer(texts, { padding: true, truncation: true, max_length: 512, ...options });
      const prepared = performance.now();
      const output = await model(inputs);
      const done = performance.now();
      const rows = validate(output.sentence_embedding);
      report.samples.push({ label, batch: texts.length, padded_tokens: inputs.input_ids.dims.at(-1),
        preprocess_ms: prepared - begin, inference_ms: done - prepared, total_ms: done - begin });
      console.log(JSON.stringify(report.samples.at(-1)));
      snapshot(`after_${label}`);
      return rows;
    }
    const docs = [
      'title: USB SSH | text: OnePlus 8T runs Debian Linux. Connect through USB using SSH public key authentication.',
      'title: 水果 | text: 苹果和香蕉是水果，可以做成水果沙拉。',
      'title: 触摸驱动 | text: 修复触摸报文长度校验，使单指双指操作正常。',
    ];
    await encode(['task: search result | query: 手机如何通过USB连接SSH？'], 'warmup');
    const corpus = await encode(docs, 'corpus_batch3');
    const queries = ['task: search result | query: 手机如何通过USB连接SSH？', 'task: search result | query: 触摸屏双指报文修复'];
    const q = await encode(queries, 'chinese_queries');
    const ranks = q.map(v => corpus.map(d => dot(v, d)));
    report.checks.chinese_retrieval = ranks.map((scores, i) => ({ scores, expected: [0, 2][i], top: scores.indexOf(Math.max(...scores)) }));
    if (report.checks.chinese_retrieval.some(x => x.expected !== x.top)) throw new Error('Chinese smoke retrieval failed');
    for (const length of shortProfile ? [32, 128] : [32, 128, 512]) {
      const text = 'title: none | text: ' + 'The Linux server stores documents and provides semantic search. '.repeat(100);
      for (let i = 0; i < (shortProfile ? 1 : 2); i++) await encode([text], `single_${length}_${i}`, { max_length: length });
      snapshot(`after_${length}`);
    }
    if (!shortProfile) await encode(Array(4).fill('title: none | text: ' + 'The Linux server stores searchable documents. '.repeat(30)), 'batch4_128', { max_length: 128 });
  } else {
    const processor = await AutoProcessor.from_pretrained(modelPath, { local_files_only: true });
    processor.image_processor.max_soft_tokens = Number(process.env.EG_VISION_TOKENS ?? 70);
    report.vision_token_budget = processor.image_processor.max_soft_tokens;
    const vectors = [];
    for (const name of ['cats.jpg', 'corgi.jpg']) {
      const image = await RawImage.read(path.join(root, 'assets', name));
      for (let i = 0; i < (shortProfile ? 1 : 2); i++) {
        await pause();
        snapshot(`before_${name}_${i}`);
        const start = performance.now();
        const inputs = await processor(null, image);
        const prepared = performance.now();
        const output = await model(inputs);
        const done = performance.now();
        const rows = validate(output.sentence_embedding);
        if (i === (shortProfile ? 0 : 1)) vectors.push(rows[0]);
        report.samples.push({ label: `${name}_${i}`, image_size: [image.width, image.height],
          padded_tokens: inputs.input_ids.dims.at(-1), preprocess_ms: prepared - start,
          inference_ms: done - prepared, total_ms: done - start });
        console.log(JSON.stringify(report.samples.at(-1)));
        snapshot(`after_${name}_${i}`);
      }
      snapshot(`after_${name}`);
    }
    report.checks.image_pair_cosine = dot(vectors[0], vectors[1]);
    if (report.checks.image_pair_cosine > .999) throw new Error('Different images returned identical embeddings');
    const texts = ['task: search result | query: cats sleeping on a couch',
      'task: search result | query: a corgi dog',
      'task: search result | query: 沙发上睡觉的猫', 'task: search result | query: 柯基犬'];
    await pause();
    snapshot('before_cross_modal_queries');
    const begin = performance.now();
    const inputs = await processor(texts);
    const output = await model(inputs);
    const rows = validate(output.sentence_embedding);
    report.query_batch_ms = performance.now() - begin;
    report.checks.cross_modal_retrieval = rows.map((v, i) => {
      const scores = vectors.map(x => dot(v, x));
      return { language: i < 2 ? 'en' : 'zh', scores, expected: i % 2, top: scores.indexOf(Math.max(...scores)) };
    });
    if (report.checks.cross_modal_retrieval.some(x => x.expected !== x.top)) throw new Error('Cross-modal smoke retrieval failed');
  }
  snapshot('completed');
  report.success = true;
} catch (e) {
  report.success = false;
  report.error = { message: e.message, stack: e.stack };
  console.error(e);
  process.exitCode = 1;
} finally {
  if (model) await model.dispose();
  snapshot('disposed');
  report.finished_at = new Date().toISOString();
  report.peak_rss_mib = process.resourceUsage().maxRSS / 1024;
  fs.mkdirSync(path.join(root, 'results'), { recursive: true });
  fs.writeFileSync(path.join(root, 'results', `${mode}.json`), JSON.stringify(report, null, 2));
}
