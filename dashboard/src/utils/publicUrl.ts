declare global {
  interface Window {
    /**
     * fork: public base injected at runtime when the page is served under a
     * prefix by a gateway (fnOS ``/app/octop-native/``). Wins over the
     * build-time ``import.meta.env.BASE_URL``.
     */
    __OCTOP_BASE__?: string;
  }
}

/** Public base path, always ending with ``/``. */
export function appBase(): string {
  const runtime =
    typeof window !== "undefined" ? window.__OCTOP_BASE__ : undefined;
  const base = runtime || import.meta.env.BASE_URL || "/";
  return base.endsWith("/") ? base : `${base}/`;
}

/** True when a gateway injected a runtime base (fork: fnOS gateway). */
export function hasRuntimeBase(): boolean {
  return typeof window !== "undefined" && Boolean(window.__OCTOP_BASE__);
}

/**
 * Resolve a path under the public base (runtime gateway base, else the
 * Vite-configured ``import.meta.env.BASE_URL``).
 * Use for static assets and SW registration when the app may be hosted under a subpath.
 */
export function publicUrl(path: string): string {
  const base = appBase();
  const cleaned = path.replace(/^\//, "");
  if (base === "/") return `/${cleaned}`;
  return `${base}${cleaned}`;
}

/** Router basename: no trailing slash; ``undefined`` when served from domain root. */
export function routerBasename(): string | undefined {
  const base = appBase();
  if (base === "/") return undefined;
  return base.replace(/\/$/, "") || undefined;
}

/** ``window.location.pathname`` without the public base (``/chat/x``). */
export function appPathname(): string {
  const path = window.location.pathname;
  const basename = routerBasename();
  if (!basename) return path;
  if (path === basename) return "/";
  return path.startsWith(`${basename}/`) ? path.slice(basename.length) : path;
}
