import { afterEach, describe, expect, it } from "vitest";
import {
  appBase,
  appPathname,
  hasRuntimeBase,
  publicUrl,
  routerBasename,
} from "./publicUrl";

describe("publicUrl", () => {
  it("prefixes the Vite base path", () => {
    expect(publicUrl("sw.js")).toMatch(/sw\.js$/);
    expect(publicUrl("/logo.png")).toMatch(/logo\.png$/);
  });
});

describe("routerBasename", () => {
  it("is undefined at domain root", () => {
    // Vitest default BASE_URL is "/".
    expect(routerBasename()).toBeUndefined();
  });
});

// fork: runtime base injected by the fnOS gateway.
describe("runtime gateway base", () => {
  afterEach(() => {
    delete window.__OCTOP_BASE__;
    window.history.replaceState(null, "", "/");
  });

  it("is off without the global", () => {
    expect(hasRuntimeBase()).toBe(false);
    expect(appBase()).toBe("/");
    expect(publicUrl("login")).toBe("/login");
  });

  it("prefixes urls, router basename and pathname", () => {
    window.__OCTOP_BASE__ = "/app/octop-native/";
    window.history.replaceState(null, "", "/app/octop-native/chat/abc");
    expect(hasRuntimeBase()).toBe(true);
    expect(publicUrl("/login")).toBe("/app/octop-native/login");
    expect(routerBasename()).toBe("/app/octop-native");
    expect(appPathname()).toBe("/chat/abc");
    window.history.replaceState(null, "", "/app/octop-native");
    expect(appPathname()).toBe("/");
  });
});
