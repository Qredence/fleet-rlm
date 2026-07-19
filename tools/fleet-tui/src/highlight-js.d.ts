declare module "highlight.js/lib/core.js" {
  const core: typeof import("highlight.js").default;
  export default core;
}

declare module "highlight.js/lib/languages/*.js" {
  const language: Parameters<typeof import("highlight.js").default.registerLanguage>[1];
  export default language;
}
