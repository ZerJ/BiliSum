"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { apiRequest, loginAccount } = require("../lib/api");

test("CLI logs in with its own account and uses only that session cookie", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    if (String(url).endsWith("/auth/login")) {
      return new Response(JSON.stringify({ authenticated: true }), {
        status: 200,
        headers: { "Set-Cookie": "bilisum_account=personal-session; HttpOnly; SameSite=Strict; Path=/" },
      });
    }
    return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
  };
  try {
    const session = await loginAccount("http://127.0.0.1:3838", "Alice", "my-password");
    assert.deepEqual(session, { sessionCookie: "bilisum_account=personal-session" });
    await apiRequest("http://127.0.0.1:3838", "/api/v1/tasks", { token: session });
    assert.equal(calls[1].options.headers.Cookie, "bilisum_account=personal-session");
    assert.equal(calls[1].options.headers.Authorization, undefined);
    assert.deepEqual(JSON.parse(calls[0].options.body), { name: "Alice", password: "my-password" });
  } finally {
    global.fetch = originalFetch;
  }
});
