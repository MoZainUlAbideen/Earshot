import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, ask } from "./api";

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status, headers })));
}

afterEach(() => vi.unstubAllGlobals());

describe("ask", () => {
  it("returns the answer on success", async () => {
    reply(200, { question: "q?", answer: "A[1].", found: true, citations: [] });
    await expect(ask("q?")).resolves.toMatchObject({ found: true, answer: "A[1]." });
  });

  it("passes the server's rate-limit message and Retry-After through", async () => {
    reply(429, { detail: "Too many questions from you; please try again later." }, { "Retry-After": "120" });
    const err = await ask("q?").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 429, retryAfter: 120, message: expect.stringContaining("Too many") });
  });

  it("turns validation errors (a list, not a sentence) into a readable message", async () => {
    reply(422, { detail: [{ loc: ["body", "question"], msg: "too short" }] });
    await expect(ask("q")).rejects.toMatchObject({ status: 422, message: expect.stringContaining("3 and 300") });
  });

  it("reports an unreachable server as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(ask("q?")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Can't reach") });
  });

  it("falls back to a generic message when the body isn't JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })));
    await expect(ask("q?")).rejects.toMatchObject({ status: 502, message: "Something went wrong. Please try again." });
  });
});
