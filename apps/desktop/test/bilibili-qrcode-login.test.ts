import assert from "node:assert/strict";
import test from "node:test";

import { runBilibiliQrcodeLogin } from "../src/bilibiliQrcodeLogin.ts";
import type { BilibiliQrcodeState } from "../src/bilibiliQrcodeLogin.ts";
import type { BilibiliQrcodePollResponse } from "../src/api.ts";

function harness(results: BilibiliQrcodePollResponse[]) {
  const controller = new AbortController();
  const states: BilibiliQrcodeState[] = [];
  const saved: string[] = [];
  let polls = 0;
  const options = {
    signal: controller.signal,
    createQrcode: async () => ({ url: "https://passport.bilibili.com/test", qrcodeKey: "qr-key", expiresIn: 180 }),
    renderQrcode: async () => "data:image/png;base64,test",
    pollQrcode: async (key: string) => {
      assert.equal(key, "qr-key");
      polls += 1;
      assert.ok(results.length, "polling should stop at terminal status");
      return results.shift()!;
    },
    onChange: (state: BilibiliQrcodeState) => { states.push(state); },
    onConfirmed: async (cookies: { cookiesFile: string; cookieCount: number }) => { saved.push(cookies.cookiesFile); },
    wait: async () => {},
  };
  return { controller, states, saved, options, polls: () => polls };
}

test("web login waits for phone confirmation and saves Cookies once", async () => {
  const h = harness([
    { status: "pending", message: "" },
    { status: "scanned", message: "" },
    { status: "confirmed", message: "", cookiesFile: "/data/cookies/bilibili.txt", cookieCount: 4 },
  ]);
  await runBilibiliQrcodeLogin(h.options);
  assert.deepEqual(h.states.map((s) => s.status), ["loading", "pending", "pending", "scanned", "saving", "confirmed"]);
  assert.ok(h.states[1]!.image.startsWith("data:image/png"));
  assert.deepEqual(h.saved, ["/data/cookies/bilibili.txt"]);
  assert.equal(h.polls(), 3);
});

test("expired QR removes image and stops polling without saving", async () => {
  const h = harness([{ status: "expired", message: "二维码已过期" }]);
  await runBilibiliQrcodeLogin(h.options);
  assert.equal(h.states.at(-1)?.status, "expired");
  assert.equal(h.states.at(-1)?.image, "");
  assert.deepEqual(h.saved, []);
  assert.equal(h.polls(), 1);
});

test("local expiry stops polling even when server keeps returning pending", async () => {
  const h = harness([]);
  await runBilibiliQrcodeLogin({ ...h.options, createQrcode: async () => ({ url: "test", qrcodeKey: "expired", expiresIn: 0 }) });
  assert.equal(h.states.at(-1)?.status, "expired");
  assert.equal(h.polls(), 0);
});

test("closing during QR creation prevents image rendering and polling", async () => {
  const h = harness([]);
  await runBilibiliQrcodeLogin({
    ...h.options,
    createQrcode: async () => {
      h.controller.abort();
      return { url: "test", qrcodeKey: "qr-key", expiresIn: 180 };
    },
    renderQrcode: async () => { assert.fail("closed login should not render QR"); },
  });
  assert.deepEqual(h.states.map((s) => s.status), ["loading"]);
  assert.equal(h.polls(), 0);
});

test("closing during a poll ignores late confirmation", async () => {
  const h = harness([]);
  await runBilibiliQrcodeLogin({
    ...h.options,
    pollQrcode: async () => {
      h.controller.abort();
      return { status: "confirmed", message: "", cookiesFile: "/data/cookies.txt" };
    },
  });
  assert.deepEqual(h.states.map((s) => s.status), ["loading", "pending"]);
  assert.deepEqual(h.saved, []);
});

test("login creation and settings save failures become retryable errors", async () => {
  const creation = harness([]);
  await runBilibiliQrcodeLogin({ ...creation.options, createQrcode: async () => { throw new Error("QR unavailable"); } });
  assert.equal(creation.states.at(-1)?.message, "QR unavailable");
  assert.equal(creation.states.at(-1)?.status, "error");
  const save = harness([{ status: "confirmed", message: "", cookiesFile: "/data/cookies.txt" }]);
  await runBilibiliQrcodeLogin({ ...save.options, onConfirmed: async () => { throw new Error("save failed"); } });
  assert.equal(save.states.at(-1)?.status, "error");
  assert.equal(save.states.at(-1)?.message, "save failed");
});

test("confirmation without a Cookies file is not treated as a successful login", async () => {
  const h = harness([{ status: "confirmed", message: "" }]);
  await runBilibiliQrcodeLogin(h.options);
  assert.equal(h.states.at(-1)?.status, "error");
  assert.deepEqual(h.saved, []);
});
