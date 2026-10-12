"use strict";

const { createInterface } = require("node:readline/promises");
const { existsSync } = require("node:fs");
const { UsageError, parseTaskCommandArgs } = require("./args");

const CONTENT_CHOICES = [
  { label: "文字笔记", aliases: ["1", "文字", "文本", "文字笔记"], visual: "text" },
  { label: "图文笔记", aliases: ["2", "图文", "图文笔记", "带图"], visual: "frame_insert" },
  { label: "快速摘要", aliases: ["3", "摘要", "快速摘要", "brief"], visual: "text", brief: true },
  { label: "转写全文", aliases: ["4", "转写", "字幕", "转写全文", "transcript"], visual: "text", transcript: true },
];
const FORMAT_CHOICES = [
  { label: "Markdown", aliases: ["1", "markdown", "md"], value: "markdown" },
  { label: "JSON", aliases: ["2", "json"], value: "json" },
];

function normalizeSource(value) {
  const trimmed = value.trim();
  if (existsSync(trimmed)) return trimmed;
  const url = trimmed.match(/https?:\/\/[^\s<>"“”]+/i)?.[0];
  if (url) return url.replace(/[，。！？）)]+$/, "");
  const bv = trimmed.match(/\bBV[a-zA-Z0-9]{10}\b/)?.[0];
  return bv ? `https://www.bilibili.com/video/${bv}` : "";
}

async function choose(ask, write, question, choices) {
  for (;;) {
    const answer = await ask(`${question}\n${choices.map((choice, index) => `${index + 1}. ${choice.label}`).join("  ")}\n> `);
    const selected = choices.find((choice) => choice.aliases.includes(answer.toLowerCase()));
    if (selected) return selected;
    write("没有识别到这个选择，请输入编号或选项名称。\n");
  }
}

// Keep the conversation separate from service startup so cancellation never creates a task.
async function collectChatTask({ source = "", args = [], ask, write }) {
  let normalized = normalizeSource(source);
  while (!normalized) {
    normalized = normalizeSource(await ask("发送视频链接、BV 号或本地文件路径（输入 q 退出）：\n> "));
    if (!normalized) write("未识别到链接或本地文件，请重新输入。\n");
  }
  const content = await choose(ask, write, "想生成哪种内容？", CONTENT_CHOICES);
  let format = content.transcript ? "transcript" : "markdown";
  if (!content.transcript && !args.some((arg) => arg === "--format" || arg === "-f")) {
    format = (await choose(ask, write, "结果用什么格式？", FORMAT_CHOICES)).value;
  }
  const taskArgs = [normalized, "--format", format, "--visual-note", content.visual, ...args];
  if (!args.some((arg) => arg === "--output" || arg === "-o")) {
    const output = await ask("保存到哪个文件？直接回车显示在终端：\n> ");
    if (output) taskArgs.push("--output", output);
  }
  const { options } = parseTaskCommandArgs(taskArgs);
  write(`\n来源：${normalized}\n内容：${content.label}\n格式：${options.format}\n输出：${options.output || "终端"}\n`);
  const confirmation = await choose(ask, write, "确认开始生成？", [
    { label: "开始生成", aliases: ["1", "y", "yes", "确认", "开始", "开始生成"], value: true },
    { label: "重新选择", aliases: ["2", "n", "no", "重新选择"], value: false },
  ]);
  return confirmation.value ? { args: taskArgs, options: { defaultFormat: format, brief: Boolean(content.brief) } } : null;
}

async function runChatCommand(args, runTask, { input = process.stdin, output = process.stderr } = {}) {
  if (args.includes("--help") || args.includes("-h")) {
    output.write("用法：bilisum chat [链接或文件] [任务选项]\n也可直接运行 bilisum <链接>。逐步选择内容、输出格式和文件，确认后生成。\n输入 q / 退出 / 取消 或按 Ctrl+C 退出。支持 summarize 的连接、分 P 和超时选项。\n");
    return;
  }
  if (!input.isTTY) throw new UsageError("对话模式需要交互终端。脚本中请使用 summarize / brief / transcribe 并指定 --format。", { showHelp: false });
  const initialSource = args[0] && !args[0].startsWith("-") ? args[0] : "";
  const taskOptions = initialSource ? args.slice(1) : args;
  parseTaskCommandArgs(["https://example.com/video", ...taskOptions]);
  const rl = createInterface({ input, output });
  const abort = new AbortController();
  rl.on("SIGINT", () => rl.close());
  rl.on("close", () => abort.abort());
  const write = (text) => output.write(text);
  const ask = async (prompt) => {
    const answer = (await rl.question(prompt, { signal: abort.signal })).trim();
    if (/^(q|quit|exit|退出|取消)$/i.test(answer)) {
      rl.close();
      throw Object.assign(new Error("cancelled"), { name: "AbortError" });
    }
    return answer;
  };
  try {
    write("BiliSum 对话式生成 · 随时输入 q 退出\n");
    let source = initialSource;
    while (!abort.signal.aborted) {
      const task = await collectChatTask({ source, args: taskOptions, ask, write });
      if (!task) continue;
      rl.pause();
      try {
        await runTask(task.args, task.options);
      } catch (error) {
        write(`生成失败：${error instanceof Error ? error.message : String(error)}\n`);
      } finally {
        rl.resume();
      }
      source = "";
    }
  } catch (error) {
    if (error.name !== "AbortError" && error.code !== "ERR_USE_AFTER_CLOSE") throw error;
    write("已退出对话。\n");
  } finally {
    rl.close();
  }
}

module.exports = { normalizeSource, collectChatTask, runChatCommand };
