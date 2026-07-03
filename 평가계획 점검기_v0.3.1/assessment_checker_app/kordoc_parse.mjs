import { parse } from "./node_modules/kordoc/dist/index.js";

const filePath = process.argv[2];

if (!filePath) {
  console.error("Usage: node kordoc_parse.mjs <document.hwpx>");
  process.exit(2);
}

try {
  const result = await parse(filePath);
  if (!result?.success) {
    console.error(result?.error || "kordoc parse failed");
    process.exit(3);
  }

  const markdown = result.markdown || "";
  if (!markdown.trim()) {
    process.exit(4);
  }

  process.stdout.write(markdown);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
