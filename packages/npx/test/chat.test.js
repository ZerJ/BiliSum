"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const { collectChatTask, normalizeSource, runChatCommand } = require("../lib/chat");
const { parseTaskCommandArgs } = require("../lib/args");

function conversation(answers, extra = {}) {
  return collectChatTask({ source: "https://youtu.be/example", write() {}, ask: async () => {
    assert.ok(answers.length, "unexpected prompt");
    return answers.shift();
  }, ...extra });
}

test("chat extracts shared links and BV identifiers without treating prose as a file", () => {
  assert.equal(normalizeSource("看看 https://youtu.be/example。"), "https://youtu.be/example");
  assert.equal(normalizeSource("BV1xx411c7mD"), "https://www.bilibili.com/video/BV1xx411c7mD");
  assert.equal(normalizeSource("hello"), "");
});
test("chat retries invalid answers and passes visual/json/output choices to task parser", async () => {
  const result = await conversation(["不知道", "图文笔记", "json", "notes.json", "确认"]);
  const { options } = parseTaskCommandArgs(result.args);
  assert.equal(options.visualNote, "frame_insert");
  assert.equal(options.format, "json");
  assert.equal(options.output, "notes.json");
  assert.equal(result.options.brief, false);
});
test("chat keeps connection and page flags and selects brief mode", async () => {
  const result = await conversation(["3", "", "1"], { args: ["--environment", "custom", "--page", "2", "--format", "json"] });
  const { options } = parseTaskCommandArgs(result.args);
  assert.equal(result.options.brief, true);
  assert.equal(options.environment, "custom");
  assert.equal(options.page, 2);
  assert.equal(options.format, "json");
});
test("transcript skips the Markdown/JSON question and honors explicit output", async () => {
  const result = await conversation(["字幕", "1"], { args: ["-o", "subtitles.txt"] });
  assert.equal(parseTaskCommandArgs(result.args).options.format, "transcript");
});
test("reselection returns no executable task", async () => {
  assert.equal(await conversation(["文字", "1", "", "2"]), null);
});
test("chat rejects non-interactive input before invoking a task", async () => {
  await assert.rejects(runChatCommand([], () => assert.fail("must not start task"), { input: { isTTY: false }, output: { write() {} } }), /交互终端/);
});
test("chat and direct-link help exit without starting services", () => {
  for (const first of ["chat", "https://youtu.be/example"]) {
    const result = spawnSync(process.execPath, [path.join(__dirname, "../bin/bilisum.js"), first, "--help"], { encoding: "utf8" });
    assert.equal(result.status, 0);
    assert.match(result.stderr, /对话|逐步选择/);
  }
});
