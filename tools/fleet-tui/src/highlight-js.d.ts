declare module "highlight.js/lib/core" {
  const core: typeof import("highlight.js").default;
  export default core;
}

declare module "highlight.js/lib/languages/*" {
  const language: Parameters<typeof import("highlight.js").default.registerLanguage>[1];
  export default language;
}
