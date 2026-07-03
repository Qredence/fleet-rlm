import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Resolve dist directory from command-line args or default
const args = process.argv.slice(2);
let distDir = path.resolve(__dirname, "../dist");
let renderUrl = "http://127.0.0.1:8000/app/workspace";

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--dist-dir" && args[i + 1]) {
    distDir = path.resolve(args[i + 1]);
    i++;
  } else if (args[i] === "--render-url" && args[i + 1]) {
    renderUrl = args[i + 1];
    i++;
  }
}

async function run() {
  const clientDir = path.join(distDir, "client");
  if (!fs.existsSync(clientDir)) {
    console.error(`ERROR: Missing frontend client dist at ${clientDir}`);
    process.exit(1);
  }

  const indexPath = path.join(clientDir, "index.html");
  if (fs.existsSync(indexPath)) {
    console.log(`OK: frontend entrypoint available at ${indexPath}`);
    process.exit(0);
  }

  let renderedHtml = null;

  // Try rendering using TanStack Start's server build
  const serverEntry = path.join(distDir, "server/server.js");
  if (fs.existsSync(serverEntry)) {
    try {
      renderedHtml = await renderStartEntrypoint(serverEntry, renderUrl);
    } catch (err) {
      // Fail silently and let the fallback mechanism generate the file
    }
  }

  if (renderedHtml) {
    fs.writeFileSync(indexPath, renderedHtml, "utf8");
    console.log(`OK: frontend entrypoint available at ${indexPath}`);
    process.exit(0);
  }

  // Fallback: Parse manifest
  try {
    const serverAssetsDir = path.join(distDir, "server/assets");
    if (!fs.existsSync(serverAssetsDir)) {
      throw new Error(`Missing TanStack Start manifest under ${serverAssetsDir}`);
    }

    const files = fs.readdirSync(serverAssetsDir);
    const manifestFiles = files
      .filter((f) => f.startsWith("_tanstack-start-manifest") && f.endsWith(".js"))
      .sort();

    if (manifestFiles.length === 0) {
      throw new Error(`Missing TanStack Start manifest under ${serverAssetsDir}`);
    }

    const manifestPath = path.join(serverAssetsDir, manifestFiles[manifestFiles.length - 1]);
    const content = fs.readFileSync(manifestPath, "utf8");

    // Extract clientEntry path
    let clientEntry = null;
    const clientEntryMatch = content.match(/clientEntry:`([^`]+)`/);
    if (clientEntryMatch) {
      clientEntry = clientEntryMatch[1];
    } else {
      const fallbackMatch = content.match(/src:`(\/assets\/index-[^`]+\.js)`/);
      if (fallbackMatch) {
        clientEntry = fallbackMatch[1];
      } else {
        throw new Error(`Could not find clientEntry or fallback index src in ${manifestPath}`);
      }
    }

    // Extract CSS paths
    const cssPaths = [];
    const cssRegex1 = /`(\/assets\/[^`]+\.css)`/g;
    let match;
    while ((match = cssRegex1.exec(content)) !== null) {
      if (!cssPaths.includes(match[1])) {
        cssPaths.push(match[1]);
      }
    }
    const cssRegex2 = /href:`(\/assets\/[^`]+\.css)`/g;
    while ((match = cssRegex2.exec(content)) !== null) {
      if (!cssPaths.includes(match[1])) {
        cssPaths.push(match[1]);
      }
    }
    cssPaths.sort();

    // Verify assets exist
    const missingAssets = [];
    for (const assetPath of [clientEntry, ...cssPaths]) {
      const fullAssetPath = path.join(clientDir, assetPath.replace(/^\//, ""));
      if (!fs.existsSync(fullAssetPath)) {
        missingAssets.push(assetPath);
      }
    }

    if (missingAssets.length > 0) {
      throw new Error(`Manifest references missing frontend assets: ${missingAssets.join(", ")}`);
    }

    const stylesheetTags = cssPaths
      .map((p) => `    <link rel="stylesheet" href="${escapeHtml(p)}" />`)
      .join("\n");

    const htmlText = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark light" />
    <title>Qredence</title>
${stylesheetTags}
    <script type="module" crossorigin src="${escapeHtml(clientEntry)}"></script>
  </head>
  <body>
    <noscript>JavaScript is required to use Fleet-RLM.</noscript>
  </body>
</html>
`;

    fs.writeFileSync(indexPath, htmlText, "utf8");
    console.log(`OK: frontend entrypoint available at ${indexPath}`);
    process.exit(0);
  } catch (err) {
    console.error(`ERROR: ${err.message}`);
    process.exit(1);
  }
}

async function renderStartEntrypoint(serverPath, url) {
  // Use Promise with a timeout
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error("Timeout rendering start entrypoint"));
    }, 5000); // 5 seconds timeout for rendering (plenty of time)

    import(pathToFileURL(serverPath).href)
      .then(async (server) => {
        const handler = server.default;
        if (!handler || typeof handler.fetch !== "function") {
          clearTimeout(timer);
          reject(new Error(`${serverPath} does not export a fetch handler`));
          return;
        }

        try {
          const response = await handler.fetch(
            new Request(url, {
              headers: {
                accept: "text/html",
                "X-TSS_SHELL": "true",
              },
            }),
          );
          if (!response.ok) {
            clearTimeout(timer);
            reject(new Error(`TanStack Start render failed with HTTP ${response.status}`));
            return;
          }
          const text = await response.text();
          clearTimeout(timer);
          if (!text.trim()) {
            reject(new Error("Empty rendered entrypoint"));
          } else {
            resolve(text);
          }
        } catch (fetchErr) {
          clearTimeout(timer);
          reject(fetchErr);
        }
      })
      .catch((err) => {
        clearTimeout(timer);
        reject(err);
      });
  });
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

run();
